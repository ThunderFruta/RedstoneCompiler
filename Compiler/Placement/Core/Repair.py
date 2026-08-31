"""Transactional packed-cluster access repair."""

from __future__ import annotations

from dataclasses import (
    dataclass,
    replace,
)
from itertools import (
    combinations,
)
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
)
from Compiler.Placement.Rotation import (
    RotatedCellSize,
)
from Compiler.Placement.Geometry import (
    BuildPlacedGate,
    PlacedGate,
    PlacedDesign,
)
from Compiler.Routing.Actions.Geometry import (
    BuildPlacedCellGeometry,
)
from .Clustering import (
    PcbGatesConflict,
    TransformPackedClusterLayout,
)
from .Clusters import (
    PcbPlacement,
)
from .MandatoryAccess import (
    CountMandatoryAccessConflicts,
    MeasureMandatoryAccessConflictProfile,
    RepairPackedClusterAccess,
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .Channels import (
        ClusterBoundaryLeaseRequest,
    )


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
    RepairEndpointGateNames: frozenset[str] = frozenset(),
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> TransactionalClusterEndpointRepairResult:
    """Repair endpoint access without reopening clustering or global slots.

    This is a physical-design ECO transaction.  It may translate or mirror
    only NAND gates touching the reported signals inside their current
    clusters.  A typed endpoint failure may instead rigidly rotate the exact
    endpoint island inside its selected packed cluster.  The transformed
    island is validated against every stationary cluster member.  Every gate
    outside an accepted endpoint island, the global XZ envelope, and
    unaffected local routes remain immutable.  Claims incident to a moved
    gate are deliberately released so authoritative routing regenerates them
    against the new pin geometry.
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
    EndpointGateNames = frozenset(
        str(Name)
        for Name in RepairEndpointGateNames
        if str(Name) in SourceGateByName
    )
    SemanticRepairTerminalPositions = frozenset(
        Pin
        for Name in EndpointGateNames
        for Gate in (SourceGateByName[Name],)
        for Signal, Pin in (
            *((OutputSignal, Gate.OutputPin) for OutputSignal in Gate.Outputs),
            *zip(Gate.Inputs, Gate.InputPins),
        )
        if str(Signal) in Signals and Pin is not None
    )
    EffectiveRepairTerminalPositions = (
        SemanticRepairTerminalPositions
        if SemanticRepairTerminalPositions
        else RepairTerminalPositions
    )
    Diagnostics['RepairEndpointGateNames'] = sorted(EndpointGateNames)
    Diagnostics['SemanticRepairTerminalPositions'] = [
        list(Position)
        for Position in sorted(SemanticRepairTerminalPositions)
    ]
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
                (
                    Name in EndpointGateNames
                    or Gate.OutputPin in EffectiveRepairTerminalPositions
                )
                and bool(set(Gate.Outputs) & Signals)
            )
            or any(
                (
                    Name in EndpointGateNames
                    or Pin in EffectiveRepairTerminalPositions
                )
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
    RigidMacroGateNames: set[str] = set()
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
                PriorityTerminalPositions=EffectiveRepairTerminalPositions,
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
                    (
                        Name in EndpointGateNames
                        or Gate.OutputPin in EffectiveRepairTerminalPositions
                    )
                    and bool(set(Gate.Outputs) & Signals)
                )
                or any(
                    (
                        Name in EndpointGateNames
                        or Pin in EffectiveRepairTerminalPositions
                    )
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
            RigidNames = PriorityEndpointNames
            RigidOriginX = min(
                RepairedPositions[Name][0]
                for Name in RigidNames
            )
            RigidOriginZ = min(
                RepairedPositions[Name][1]
                for Name in RigidNames
            )
            SourceRigidWidth = max(
                RepairedPositions[Name][0]
                + RotatedCellSize(
                    InternalByName[Name].Kind,
                    LocalRotations[Name],
                )[0]
                for Name in RigidNames
            ) - RigidOriginX
            SourceRigidDepth = max(
                RepairedPositions[Name][1]
                + RotatedCellSize(
                    InternalByName[Name].Kind,
                    LocalRotations[Name],
                )[1]
                for Name in RigidNames
            ) - RigidOriginZ
            RigidVariant = TransformPackedClusterLayout(
                tuple(RigidNames),
                {
                    Name: (
                        RepairedPositions[Name][0] - RigidOriginX,
                        RepairedPositions[Name][1] - RigidOriginZ,
                    )
                    for Name in RigidNames
                },
                {
                    Name: LocalRotations[Name]
                    for Name in RigidNames
                },
                {
                    Name: RepairedMirrors[Name]
                    for Name in RigidNames
                },
                RotationDelta,
                False,
                GatesByName={
                    Name: InternalByName[Name]
                    for Name in RigidNames
                },
            )
            RigidWidthDelta = SourceRigidWidth - RigidVariant.Width
            RigidDepthDelta = SourceRigidDepth - RigidVariant.Depth
            RigidAnchorVariant = (ClusterRepairVariant // 3) % 4
            RigidAnchorOffsetX = (
                (RigidWidthDelta + 1) // 2
                if RigidAnchorVariant & 1
                else RigidWidthDelta // 2
            )
            RigidAnchorOffsetZ = (
                (RigidDepthDelta + 1) // 2
                if RigidAnchorVariant & 2
                else RigidDepthDelta // 2
            )
            CandidatePositions = {
                Name: (
                    RigidOriginX
                    + RigidAnchorOffsetX
                    + RigidVariant.Positions[Name][0],
                    RigidOriginZ
                    + RigidAnchorOffsetZ
                    + RigidVariant.Positions[Name][1],
                )
                for Name in RigidNames
            } if RigidVariant.IsLegal else {}
            CandidateRotations = {
                Name: RigidVariant.Rotations[Name]
                for Name in RigidNames
            } if RigidVariant.IsLegal else {}
            CandidateMirrors = {
                Name: RigidVariant.Mirrors[Name]
                for Name in RigidNames
            } if RigidVariant.IsLegal else {}
            CandidateClusterGates = [
                BuildPlacedGate(
                    InternalByName[Name],
                    (
                        CandidatePositions[Name][0]
                        if Name in CandidatePositions
                        else RepairedPositions[Name][0]
                    ),
                    SourceGateByName[Name].Y,
                    (
                        CandidatePositions[Name][1]
                        if Name in CandidatePositions
                        else RepairedPositions[Name][1]
                    ),
                    CandidateRotations.get(
                        Name,
                        LocalRotations[Name],
                    ),
                    CandidateMirrors.get(
                        Name,
                        RepairedMirrors[Name],
                    ),
                )
                for Name in ClusterNames
            ] if RigidVariant.IsLegal else []
            RigidRotationAccepted = bool(
                RigidVariant.IsLegal
                and not any(
                    PcbGatesConflict(First, Second)
                    for GateIndex, First in enumerate(CandidateClusterGates)
                    for Second in CandidateClusterGates[GateIndex + 1 :]
                )
                and CountMandatoryAccessConflicts(
                    CandidateClusterGates,
                    ClusterSignals,
                ) == 0
            )
            if RigidRotationAccepted:
                RepairedPositions.update(CandidatePositions)
                RepairedRotationByName.update(CandidateRotations)
                RepairedMirrors.update(CandidateMirrors)
                RigidMacroGateNames.update(RigidNames)
                ClusterDiagnostics["PriorityEndpointRotationDelta"] = (
                    RotationDelta
                )
                ClusterDiagnostics["PriorityEndpointRotationNames"] = (
                    list(PriorityEndpointNames)
                )
                ClusterDiagnostics["PriorityEndpointRigidRotation"] = True
                ClusterDiagnostics["PriorityEndpointRigidRotationSize"] = [
                    RigidVariant.Width,
                    RigidVariant.Depth,
                ]
                ClusterDiagnostics["PriorityEndpointRigidSourceSize"] = [
                    SourceRigidWidth,
                    SourceRigidDepth,
                ]
                ClusterDiagnostics["PriorityEndpointRigidAnchorVariant"] = (
                    RigidAnchorVariant
                )
                ClusterDiagnostics["PriorityEndpointRigidAnchorOffset"] = [
                    RigidAnchorOffsetX,
                    RigidAnchorOffsetZ,
                ]
            else:
                ClusterDiagnostics["PriorityEndpointRotationRejected"] = (
                    True
                )
                ClusterDiagnostics[
                    "PriorityEndpointRotationRejectionReason"
                ] = (
                    RigidVariant.RejectionReason
                    or "rigid-macro-conflict-or-mandatory-access"
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
    ) | frozenset(RigidMacroGateNames)
    UnexpectedChanges = ChangedGateNames - AllowedGateNames
    if UnexpectedChanges:
        Diagnostics.update({
            "Reason": "unrelated-gate-geometry-changed",
            "UnexpectedChangedGateCount": len(UnexpectedChanges),
            "UnexpectedChangedGateNames": sorted(UnexpectedChanges),
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
        "RigidMacroGateCount": len(RigidMacroGateNames),
        "RigidMacroGateNames": sorted(RigidMacroGateNames),
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
