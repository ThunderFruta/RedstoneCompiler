"""Translation-safe completed component-template cache and instantiation."""

from __future__ import annotations

from collections import deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from hashlib import sha256
from itertools import product
from math import prod
import multiprocessing
import os
from time import monotonic
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping
from ...Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from ...Contracts.Component import ComponentRoutingProblem, ComponentRoutingSolveResult, PhysicalComponentAssemblyPlan, PhysicalComponentChannelReservation, PhysicalComponentPortReservation, PhysicalComponentSelectedLocalPortSupport, RoutedComponentNet, RoutedComponentTemplate
from ...Contracts.Core import Position3
from ...Contracts.PhysicalInterface import PhysicalComponentLocalFactorProjection, PhysicalComponentLocalFactorProjectionComparison, PhysicalComponentLocalFactorUnsatCertificate, PhysicalLocalPortPairProofRecord, PhysicalLocalPortPairSupportCertificate, PhysicalComponentSymbolicHigherOrderCertificate, PhysicalComponentSymbolicPortPairCertificate, PhysicalPortCorridorDomain, PhysicalPortCorridorFactor, PreparedPhysicalComponentAssembly, PreparedPhysicalComponentPortFactorDomain
from ...Interfaces import BoundaryRelations
from ...Interfaces.BoundaryRelations import BuildPhysicalPortGlobalContractFingerprint, ProjectPhysicalComponentSignalGlobalProfile
from ...Interfaces.PhysicalClaims import ComponentClaimsConflict
from ...Resources.ResourceGraph import RoutingResourceClaims
from ...Execution.Reliability import BuildStableFingerprint
from .Planning.InterfacePlanning import BuildComponentCapacityGuide, ComponentCapacityGuide, ComponentCapacityGuideOption, ComponentInterfaceContract, ComponentPlanningResult, ComponentPlanningStatus, IterClosedComponentContracts, PlanClosedComponent, SolveComponentInterfaceCsp

from .Core import BuildCompleteComponentNetPortfolioStaticContext
from .Symbolic.SymbolicState import _BuildPreparedComponentSymbolicNetStateContextFingerprint, BuildComponentSymbolicNetStateCacheKey, PrepareComponentSymbolicNetStateContext
from .Symbolic.SymbolicWorkers import CompilePreparedComponentPhysicalFactorStateBatch, CompilePreparedComponentSymbolicNetStates
from .Planning.Portfolios import BuildCompleteOpposingNetAccessContractDomain, BuildCompleteOpposingNetAccessRowContext, CompileCompleteComponentNetVariantPortfolio, CompileCompleteComponentNetVariantPortfolios, EvaluateCompleteOpposingNetAccessContractRow
from .Solving.Solver import MaterializeRoutedComponentTemplate, SolveComponentRoutingProblem, ValidateRoutedComponentHandoff

from .Proofs.Validation import _BuildSignalTranslation, _Fingerprint, _Move, _MoveClaims, _Normalize, _NormalizedClaimsIdentity, _Origin, _SignalStructuralIdentities
_CompletedComponentTemplateCache: dict[
    str,
    tuple[
        Position3,
        RoutedComponentTemplate,
        tuple[tuple[str, str], ...],
    ],
] = {}

def BuildCompletedComponentTemplateCacheFingerprint(
    Problem: ComponentRoutingProblem,
) -> str:
    """Identify topology/port/technology-equivalent component compiles."""
    if Problem.Interface is None:
        raise ValueError("closed interface required for template caching")
    Origin = _Origin(Problem)

    def NormalizeValues(
        Values: Any,
    ) -> tuple[Position3, ...]:
        return tuple(sorted(
            _Normalize(Value, Origin) for Value in Values
        ))

    DomainIdentity = tuple(sorted(
        (
            Domain.TerminalRole,
            _Normalize(Domain.Terminal, Origin),
            tuple(sorted(
                (
                    _Normalize(Candidate.Attachment, Origin),
                    NormalizeValues(Candidate.Path),
                    NormalizeValues(Candidate.Claims.WireCells),
                    NormalizeValues(Candidate.Claims.SupportCells),
                    NormalizeValues(
                        Candidate.Claims.RequiredAirCells
                    ),
                    NormalizeValues(
                        Candidate.Claims.ElectricalCells
                    ),
                    Candidate.Layer,
                )
                for Candidate in Domain.Candidates
            )),
        )
        for Domain in Problem.OwnedTerminalDomains
    ))
    ClaimIdentity = tuple(sorted(
        (
            "component"
            if Claim.Signal in Problem.ComponentSignals
            else "foreign",
            NormalizeValues(Claim.Claims.WireCells),
            NormalizeValues(Claim.Claims.SupportCells),
            NormalizeValues(Claim.Claims.RequiredAirCells),
            NormalizeValues(Claim.Claims.ElectricalCells),
        )
        for Claim in (
            *Problem.LocalClaims,
            *Problem.ImmutableClaims,
        )
    ))
    SignalIdentityByName = dict(_SignalStructuralIdentities(Problem))

    def AssemblySignalIdentity(Signal: str) -> str:
        return SignalIdentityByName.get(
            Signal,
            "foreign-global-channel",
        )

    LogicalInterfaceIdentity = (
        Problem.Interface.Complete,
        tuple(sorted(
            (
                AssemblySignalIdentity(Port.Signal),
                Port.Direction,
                tuple(sorted(
                    _Normalize(Value, Origin)
                    for Value in Port.OwnedTerminals
                )),
                Port.ExternalTerminalCount,
                Port.Capacity,
            )
            for Port in Problem.Interface.Ports
        )),
        tuple(sorted(
            AssemblySignalIdentity(Signal)
            for Signal
            in Problem.Interface.DeclaredFeedthroughSignals
        )),
    )

    Plan = Problem.PhysicalAssemblyPlan
    PhysicalContractIdentity = (
        (
            tuple(sorted(
                (
                    AssemblySignalIdentity(Port.Signal),
                    Port.Direction,
                    tuple(
                        _Normalize(Value, Origin)
                        for Value in Port.OwnedTerminals
                    ),
                    _Normalize(Port.FabricAttachment, Origin),
                    _Normalize(Port.Attachment, Origin),
                    tuple(
                        _Normalize(Value, Origin)
                        for Value in Port.LocalPath
                    ),
                    tuple(
                        _Normalize(Value, Origin)
                        for Value in Port.GlobalPath
                    ),
                    _NormalizedClaimsIdentity(
                        Port.Claims,
                        Origin,
                    ),
                    Port.Capacity,
                )
                for Port in Plan.Ports
            )),
            tuple(sorted(
                (
                    AssemblySignalIdentity(Channel.Signal),
                    Channel.Layer,
                    tuple(sorted(
                        (
                            X - Origin[0],
                            Z - Origin[2],
                        )
                        for X, Z in Channel.GuideCells
                    )),
                    _NormalizedClaimsIdentity(
                        Channel.Claims,
                        Origin,
                    ),
                    Channel.Capacity,
                    len(Channel.FeedthroughComponentIds),
                )
                for Channel in Plan.Channels
            )),
            tuple(sorted(
                (
                    AssemblySignalIdentity(Feedthrough.Signal),
                    tuple(sorted(
                        (
                            _Normalize(Entry, Origin),
                            _Normalize(Exit, Origin),
                        )
                        for Entry, Exit
                        in Feedthrough.EndpointPairs
                    )),
                    Feedthrough.Capacity,
                )
                for Feedthrough in Plan.Feedthroughs
            )),
        )
        if Plan is not None
        else ()
    )
    Technology = getattr(Problem.ResourceGraph, "Technology", None)
    return _Fingerprint((
        "completed-component-template-v3",
        LogicalInterfaceIdentity,
        Problem.Fabric.FabricFingerprint,
        DomainIdentity,
        ClaimIdentity,
        tuple(sorted(SignalIdentityByName.values())),
        PhysicalContractIdentity,
        Problem.MaximumPowerDistance,
        repr(Technology),
    ))


def _MoveNet(
    Value: RoutedComponentNet,
    Delta: Position3,
    Signal: str | None = None,
) -> RoutedComponentNet:
    Claims = _MoveClaims(Value.Claims, Delta)
    Nodes = frozenset(_Move(Position, Delta) for Position in Value.Nodes)
    Edges = frozenset(
        tuple(sorted((_Move(First, Delta), _Move(Second, Delta))))
        for First, Second in Value.Edges
    )
    Repeaters = tuple(
        (_Move(Position, Delta), Facing)
        for Position, Facing in Value.RepeaterInputFacings
    )
    ExportedPorts = tuple(
        _Move(Position, Delta) for Position in Value.ExportedPorts
    )
    CoveredTerminals = tuple(
        _Move(Position, Delta) for Position in Value.CoveredTerminals
    )
    return replace(
        Value,
        Signal=Signal or Value.Signal,
        Root=_Move(Value.Root, Delta),
        Nodes=Nodes,
        Edges=Edges,
        WireCells=Claims.WireCells - frozenset(
            Position for Position, _Facing in Repeaters
        ),
        SupportCells=Claims.SupportCells,
        RepeaterInputFacings=Repeaters,
        Claims=Claims,
        CoveredTerminals=CoveredTerminals,
        ExportedPorts=ExportedPorts,
        NetFingerprint=_Fingerprint((
            tuple(sorted(Nodes)),
            tuple(sorted(Edges)),
            Repeaters,
            ExportedPorts,
        )),
    )


def _InstantiateCachedTemplate(
    Problem: ComponentRoutingProblem,
    CachedOrigin: Position3,
    Cached: RoutedComponentTemplate,
    CachedSignalIdentities: tuple[tuple[str, str], ...],
    CacheFingerprint: str,
) -> RoutedComponentTemplate | None:
    if (
        Cached.ForeignEscapeReservations
        or Cached.ExternalContinuationReservations
    ):
        return None
    TargetOrigin = _Origin(Problem)
    Delta = tuple(
        TargetOrigin[Index] - CachedOrigin[Index]
        for Index in range(3)
    )
    SignalTranslation = _BuildSignalTranslation(
        CachedSignalIdentities,
        _SignalStructuralIdentities(Problem),
    )
    if SignalTranslation is None:
        return None
    Nets = tuple(
        _MoveNet(
            Value,
            Delta,
            SignalTranslation.get(Value.Signal),
        )
        for Value in Cached.Nets
    )
    ExpectedTerminalsBySignal = {
        Signal: tuple(sorted(
            Domain.Terminal
            for Domain in Problem.OwnedTerminalDomains
            if Domain.Signal == Signal
        ))
        for Signal in Problem.ComponentSignals
    }
    if any(
        tuple(sorted(Net.CoveredTerminals))
        != ExpectedTerminalsBySignal.get(Net.Signal, ())
        for Net in Nets
    ):
        return None
    ForeignTransits = tuple(
        _MoveNet(
            Value,
            Delta,
            SignalTranslation.get(Value.Signal),
        )
        for Value in Cached.ForeignTransitReservations
    )
    Claims = RoutingResourceClaims(
        WireCells=frozenset().union(*(
            Value.Claims.WireCells
            for Value in (*Nets, *ForeignTransits)
        )),
        SupportCells=frozenset().union(*(
            Value.Claims.SupportCells
            for Value in (*Nets, *ForeignTransits)
        )),
        RequiredAirCells=frozenset().union(*(
            Value.Claims.RequiredAirCells
            for Value in (*Nets, *ForeignTransits)
        )),
        ElectricalCells=frozenset().union(*(
            Value.Claims.ElectricalCells
            for Value in (*Nets, *ForeignTransits)
        )),
    )
    if Problem.ResourceGraph is not None:
        for Value in (*Nets, *ForeignTransits):
            ExpectedClaims = Problem.ResourceGraph.BuildRouteClaims(
                Value.Nodes
            )
            # Repeater materialization intentionally replaces the generic
            # dust electrical exclusions while retaining the same physical
            # wire, support, and required-air ownership.  Comparing the full
            # generic claim would therefore reject a valid translated cached
            # template whenever it contains a repeater.
            if (
                ExpectedClaims.WireCells != Value.Claims.WireCells
                or ExpectedClaims.SupportCells
                != Value.Claims.SupportCells
                or ExpectedClaims.RequiredAirCells
                != Value.Claims.RequiredAirCells
            ):
                return None
    ExportedPorts = tuple(sorted(
        (Net.Signal, Position)
        for Net in Nets
        for Position in Net.ExportedPorts
    ))
    Diagnostics = {
        **Cached.Diagnostics,
        "CompletedTemplateCacheHit": True,
        "CompletedTemplateCacheFingerprint": CacheFingerprint,
        "CompletedTemplateTranslationDelta": list(Delta),
    }
    RoutedFingerprint = _Fingerprint((
        Problem.ProblemFingerprint,
        tuple(Value.NetFingerprint for Value in Nets),
        tuple(Value.NetFingerprint for Value in ForeignTransits),
        ExportedPorts,
    ))
    return replace(
        Cached,
        ProblemFingerprint=Problem.ProblemFingerprint,
        PlacementFingerprint=Problem.PlacementFingerprint,
        LocalTemplateFingerprint=Problem.LocalTemplateFingerprint,
        FabricFingerprint=Problem.Fabric.FabricFingerprint,
        RoutedTemplateFingerprint=RoutedFingerprint,
        Nets=Nets,
        ExportedPorts=ExportedPorts,
        Claims=Claims,
        ProofFingerprint=_Fingerprint((
            RoutedFingerprint,
            "completed-template-cache",
        )),
        ExpansionCount=0,
        Diagnostics=Diagnostics,
        ForeignTransitReservations=ForeignTransits,
        InterfaceFingerprint=Problem.Interface.InterfaceFingerprint,
    )
