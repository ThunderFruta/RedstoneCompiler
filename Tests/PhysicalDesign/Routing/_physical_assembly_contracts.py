"""Shared fixtures and imports for split physical assembly tests."""

from dataclasses import replace

from types import SimpleNamespace

import pytest

import PhysicalDesign.Routing.Regions.Cache as ComponentCache

import PhysicalDesign.Routing.Regions.Proofs.Certification as ComponentCertification

import PhysicalDesign.Routing.Regions.Proofs.NoGoods as ComponentNoGoods

import PhysicalDesign.Routing.Regions.Planning.PhysicalPlanning as ComponentPhysicalPlanning

import PhysicalDesign.Routing.Regions.Pipeline as ComponentAssemblyPipeline

import PhysicalDesign.Routing.Regions.Planning.Portfolios as ComponentPortfolios

import PhysicalDesign.Routing.Regions.Proofs.Validation as ComponentValidation

import PhysicalDesign.Routing.Global.Ports.PortPreparationFactors as PhysicalPortPreparationFactors

import PhysicalDesign.Routing.Global.Ports.PortPreparationHelpers as PhysicalPortPreparationHelpers

import PhysicalDesign.Routing.Global.Ports.PortPreparationInputs as PhysicalPortPreparationInputs

from PhysicalDesign.Geometry.Placement import PlacedDesign

from PhysicalDesign.Flow.Candidates import BuildRetainedComponentPlacementSearchDomain, ReuseRetainedPlacementRoutingResources

from PhysicalDesign.Flow.Results import BuildPhysicalComponentPlacementFeedback, IsComponentKeepoutGlobalFailure

from PhysicalDesign.Flow.Runner import _PlaceAndRoutePcbWithPolicy

from PhysicalDesign.Flow.PhysicalFlow import RunPhysicalComponentFlow

from PhysicalDesign.Flow.PhysicalAssembly import PendingJointPlacementStateMatchesPhysicalProof, SelectCapacityRepairGeometryConstraint, SelectCapacityRepairGeometryFocus, SelectFreshProofGuidedPlacementCandidate

from PhysicalDesign.Routing.Global.Ports.ExteriorConnectors import BuildPhysicalBoundaryPortAssignmentFingerprint, BuildPhysicalGlobalApertureSearchKey, BuildPortablePhysicalGlobalApertureContract, IterPhysicalBoundaryPortAssignments, PreparePhysicalGlobalApertureStaticContract, MaterializePhysicalGlobalAperturePath, NormalizePhysicalGlobalAperturePath, RetainPhysicalGlobalAperturePathTemplate, SelectPhysicalFactorBranchSignal, SelectPhysicalBoundaryPortAssignment

from PhysicalDesign.Routing.Global.Guides.PhysicalGuides import BuildComponentKeepoutAvoidingGlobalGuides, BuildComponentKeepoutGuideCellsByLayer, BuildExplicitPhysicalComponentFeedthrough, BuildPhysicalExteriorApertureFabric, ExpandPhysicalComponentGuideChannels, FindSignalClaimConflicts, PreparePhysicalComponentFeedthroughEndpointDomain, RemoveClosedComponentInternalGuides

from PhysicalDesign.Routing.Global.Assignment.AssignmentState import BuildPhysicalPortNoGoodKeys

from PhysicalDesign.Routing.Global.Materialization import BuildPhysicalComponentAssemblyPlan

from PhysicalDesign.Routing.Global.Candidates.CandidateGuides import PropagateLaneFactorArcConsistency

from PhysicalDesign.Routing.Global.Ports.PortPreparation import PreparePhysicalComponentPortFactorDomain

from PhysicalDesign.Routing.Global.Ports.PortPreparationHelpers import SelectCertifiedStraightExteriorTargets

from PhysicalDesign.Routing.Global.Ports.PortPreparationFactors import BuildPhysicalPortLaneFactors

from PhysicalDesign.Routing.Global.Candidates.CandidateCache import TransformPlanarRoutingPosition

from PhysicalDesign.Routing.Global.Ports.Solving import SolvePreparedPhysicalComponentPortFactorDomain

from PhysicalDesign.Routing.Global.Ports.Solving.Search import _SolvePreparedPhysicalComponentPortFactorDomain

from PhysicalDesign.Routing.Planning.ChannelPlanner import ChannelPlan

from PhysicalDesign.Routing.Planning.LocalFirst import CoarseGuidePlan

from PhysicalDesign.Routing.Regions.Planning.PhysicalPlanning import BindPhysicalComponentAssemblyGlobalChannels, BindPhysicalComponentAssemblyLocalPortSupports, MaterializePreparedPhysicalPortOptionDomains

from PhysicalDesign.Routing.Regions.Proofs.NoGoods import BuildUniversalPromotedFabricPortAssignmentFailure, RecordPhysicalComponentLocalCompilationNoGood

from PhysicalDesign.Routing.Regions.Proofs.Certification import BuildDirectionalLocalFactorNoGoods, BuildPhysicalLocalPortPairSupportCertificate, BuildPhysicalLocalPairProofContextFingerprint, CertifyDirectionalLocalContractPortfolio, PromoteCoveredLocalContractNoGoods

from PhysicalDesign.Routing.Regions.Proofs.Validation import BuildPhysicalPortApertureContractFingerprint, BuildPhysicalPortLocalContractFingerprint, BuildPhysicalPortSeamContractFingerprint, ValidatePhysicalBoundaryPortHandoff, ValidatePhysicalExteriorFabricHandoff

from PhysicalDesign.Routing.Regions.Pipeline import CompileClosedComponent

from PhysicalDesign.Routing.Regions.Interfaces.Reservations import FinalizePhysicalComponentChannelReservations

from PhysicalDesign.Interfaces.BoundaryRelations import BuildPhysicalPortGlobalContractFingerprint

from PhysicalDesign.Routing.Regions.Interfaces.Access import BuildComponentAccessGuideTargetColumns, BuildComponentCutAccessFeasibilityCertificate, SelectStraightContinuationEgressDirections, ValidateComponentAccessCertificateIdentity

from PhysicalDesign.Routing.Regions.Interfaces.Fabric import AugmentComponentRoutingFabric, BuildComponentEgressPaths, BuildComponentRoutingFabric, SelectGuideFacingComponentEgressDirections

from PhysicalDesign.Routing.Regions.Core import BuildCompleteComponentNetPortfolioStaticContext

from PhysicalDesign.Routing.Regions.Planning.Portfolios import CompileCompleteComponentNetVariantPortfolio, CompileCompleteComponentNetVariantPortfolios, GetCachedCompleteComponentNetVariantPortfolio

from PhysicalDesign.Contracts.Failures import RoutingFailure, RoutingFailureReason, RoutingStageError

from PhysicalDesign.Contracts.Component import ClosedComponentInterface, ComponentFeedthroughContract, ComponentInterfacePort, ComponentPerimeterPortCandidate, ComponentPortBankDomain, ComponentRoutingProblem, ComponentRoutingSolveResult, ComponentTerminalAccessCandidate, ComponentTerminalAccessDomain, PhysicalComponentChannelReservation, PhysicalComponentBoundaryPortReservation

from PhysicalDesign.Contracts.PhysicalInterface import PhysicalGlobalAperturePathTemplate, PhysicalLocalPortPairProofRecord

from PhysicalDesign.Contracts.Results import RoutingResources

from PhysicalDesign.Resources.ResourceGraph import RoutingResourceClaims, RoutingResourceGraph, RoutingResourceId, RoutingResourceKind

from PhysicalDesign.Execution.Reliability import BuildStableFingerprint

from PhysicalDesign.Routing.Pcb import ClassifyPhysicalComponentAssemblyFailure, ReplanPhysicalComponentAssembly

from PhysicalDesign.Execution.Reliability import RoutingDeadline

from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology

def _Claims(Nodes):
    Nodes = frozenset(Nodes)
    return RoutingResourceClaims(
        WireCells=Nodes,
        SupportCells=frozenset(
            (X, Y - 1, Z) for X, Y, Z in Nodes
        ),
        ElectricalCells=Nodes,
    )

def _BoundaryPort(Signal, X):
    Attachment = (X, 7, 0)
    GlobalPath = (Attachment, (X + 1, 7, 0))
    Port = SimpleNamespace(
        Direction="output",
        Attachment=Attachment,
        GlobalPath=GlobalPath,
        GlobalClaims=_Claims(GlobalPath),
        Capacity=1,
    )
    return PhysicalComponentBoundaryPortReservation(
        Signal=Signal,
        Direction=Port.Direction,
        Attachment=Attachment,
        GlobalPath=GlobalPath,
        GlobalClaims=Port.GlobalClaims,
        Capacity=1,
        ChannelContractFingerprint=f"channel:{Signal}",
        GlobalContractFingerprint=(
            BuildPhysicalPortGlobalContractFingerprint(Port)
        ),
        ApertureContractFingerprint=(
            BuildPhysicalPortApertureContractFingerprint(Port)
        ),
        ReservationFingerprint=f"boundary:{X}",
    )

def _BoundaryAttachmentIndexSequence(Assignments):
    Signals = tuple(sorted(
        Value.Signal
        for Value in Assignments[0]
    ))
    DomainAttachments = {
        Signal: tuple(sorted({
            Value.Attachment
            for Assignment in Assignments
            for Value in Assignment
            if Value.Signal == Signal
        }))
        for Signal in Signals
    }
    return tuple(
        tuple(
            DomainAttachments[Signal].index(next(
                Value.Attachment
                for Value in Assignment
                if Value.Signal == Signal
            ))
            for Signal in Signals
        )
        for Assignment in Assignments
    )

class _ResourceGraph:
    GraphVersion = "test-resource-graph"
    Technology = DefaultRedstoneRoutingTechnology
    Nodes = ()
    Edges = ()

    def BuildRouteClaims(self, Nodes):
        return _Claims(Nodes)

    def BuildPrimitive(self, _First, _Second):
        return object()

def _Problem(Signal="Alpha", Delta=(0, 0, 0)):
    def Move(Position):
        return tuple(
            Position[Index] + Delta[Index] for Index in range(3)
        )

    Cells = tuple(map(Move, (
        (0, 7, 0),
        (1, 7, 0),
        (2, 7, 0),
    )))
    Channel = SimpleNamespace(
        PhysicalModel="test-tree",
        ComponentId=3,
        InterfaceFingerprint="logical-interface",
        DeclaredFeedthroughSignals=(),
        AffectedClusters=(0,),
        AffectedSignals=(Signal,),
        Lanes=(
            SimpleNamespace(
                Cells=Cells,
                IngressNodes=(Cells[0], Cells[-1]),
            ),
        ),
    )
    Fabric = BuildComponentRoutingFabric(Channel)

    def Candidate(Terminal):
        return ComponentTerminalAccessCandidate(
            CandidateFingerprint=f"{Signal}:{Terminal}",
            Attachment=Terminal,
            Path=(Terminal,),
            Claims=_Claims((Terminal,)),
        )

    Source = Cells[0]
    Target = Cells[-1]
    Interface = ClosedComponentInterface(
        InterfaceFingerprint="translation-stable-interface",
        ComponentId=3,
        OwnedSignals=(Signal,),
        Ports=(
            ComponentInterfacePort(
                Signal=Signal,
                Direction="output",
                OwnedTerminals=(Source, Target),
                ExternalTerminalCount=1,
            ),
        ),
    )
    return ComponentRoutingProblem(
        ProblemFingerprint="problem",
        PlacementFingerprint=f"placement:{Delta}",
        LocalTemplateFingerprint="local",
        SelectedClusters=(0,),
        ComponentSignals=(Signal,),
        LocalClaims=(),
        Fabric=Fabric,
        OwnedTerminalDomains=(
            ComponentTerminalAccessDomain(
                Signal=Signal,
                Terminal=Source,
                TerminalRole="source",
                TerminalFingerprint="source",
                Candidates=(Candidate(Source),),
            ),
            ComponentTerminalAccessDomain(
                Signal=Signal,
                Terminal=Target,
                TerminalRole="target",
                TerminalFingerprint="target",
                Candidates=(Candidate(Target),),
            ),
        ),
        ExternalContinuationTerminals=(
            (Signal, Move((12, 7, 0)), "target"),
        ),
        ForeignEscapeDomains=(),
        MaximumPowerDistance=15,
        DomainComplete=True,
        ResourceGraph=_ResourceGraph(),
        Interface=Interface,
    )

def _Guide(Problem):
    Signal = Problem.ComponentSignals[0]
    Position = Problem.Fabric.Nodes[-1]
    Resource = RoutingResourceId(
        RoutingResourceKind.Wire,
        Position,
    )
    return ChannelPlan(
        Profiles={},
        SignalOrder=(Signal,),
        TrunkSignals=frozenset(),
        Guides={Signal: frozenset(
            (X, Position[2])
            for X in range(Position[0], Position[0] + 21)
        )},
        CorridorUsage={},
        CorridorCosts={},
        CorridorCapacity=1,
        Layers={Signal: 0},
        ResourceUsage={Resource: 1},
        ResourceOverflow={},
        ResourceClaimsBySignal={Signal: frozenset((Resource,))},
        SourceAccessTransitions={},
        TargetAccessTransitions={},
    )

def _Placed(Problem):
    Signal = Problem.ComponentSignals[0]
    return SimpleNamespace(
        ComponentGraph=SimpleNamespace(
            StructuralFingerprint="component-graph",
            Channels=(
                SimpleNamespace(
                    Signal=Signal,
                    FeedthroughComponentIds=(),
                ),
            ),
        ),
    )

def _AccessCertificate(Problem, Placed, Resources):
    MinimumPlacementY = min(
        Value[1] for Value in Problem.Fabric.Nodes
    ) - 7
    return BuildComponentCutAccessFeasibilityCertificate(
        Problem,
        Resources.ResourceGraph,
        LayerCount=1,
        MinimumPlacementY=MinimumPlacementY,
        ComponentGraphFingerprint=(
            Placed.ComponentGraph.StructuralFingerprint
        ),
    )

def _Assembly(Problem, Resources=None):
    Resources = Resources or RoutingResources(
        StaticGeometry=SimpleNamespace(),
        ResourceGraph=Problem.ResourceGraph,
    )
    Placed = _Placed(Problem)
    Certificate = _AccessCertificate(Problem, Placed, Resources)
    return BuildPhysicalComponentAssemblyPlan(
        Placed,
        Problem,
        _Guide(Problem),
        Resources,
        AccessCertificate=Certificate,
    )

def _BindAssemblyForLocalCompilation(Assembly):
    Candidates = {}
    for Port in Assembly.Plan.GlobalBoundaryPorts:
        Nodes = frozenset(Port.GlobalPath)
        Candidates[Port.Signal] = SimpleNamespace(
            CandidateId=f"test-global:{Port.Signal}",
            Layer=0,
            Guide=frozenset((Position[0], Position[2]) for Position in Nodes),
            Nodes=Nodes,
            Claims=Assembly.Problem.ResourceGraph.BuildRouteClaims(Nodes),
            SourcePortalId=f"test-source:{Port.Signal}",
            TargetPortalIds={},
            RepeaterWaypoints=(),
        )
    GloballyBound = BindPhysicalComponentAssemblyGlobalChannels(
        Assembly,
        SimpleNamespace(RoutingAssignment=SimpleNamespace(
            SelectedCandidates=Candidates,
        )),
        Assembly.Problem.ResourceGraph,
    )
    return BindPhysicalComponentAssemblyLocalPortSupports(GloballyBound)

def _PreparedFactorDomainFixture(DomainFingerprint, **Domains):
    CacheKey = ComponentPhysicalPlanning.BuildPhysicalComponentPortSolverCacheKey(
        DomainFingerprint
    )
    return {
        "PreparedPhysicalComponentPortFactorDomain": SimpleNamespace(
            DomainFingerprint=DomainFingerprint,
            Complete=True,
            Feasible=True,
            ComponentGraphFingerprint="component-graph",
            ResourceGraphFingerprint="resource-graph",
            Problem=SimpleNamespace(Fabric=SimpleNamespace(
                FabricFingerprint="fabric",
            )),
            AccessCertificate=SimpleNamespace(
                TechnologyFingerprint="technology",
            ),
        ),
        "PhysicalComponentFactorPortOptionDomainCache": {
            (CacheKey, Signal): tuple(Options)
            for Signal, Options in Domains.items()
        },
    }

def _PairProofRecords(
    CurrentSignal,
    CurrentContracts,
    CompleteSignal,
    CompleteContract,
):
    return tuple(
        PhysicalLocalPortPairProofRecord(
            CurrentSignal=CurrentSignal,
            CurrentContract=CurrentContract,
            CompleteSignal=CompleteSignal,
            CompleteContract=CompleteContract,
            ProofDomainFingerprint="domain:" + CurrentContract,
            ProofFingerprint="proof:" + CurrentContract,
            Status="architectural-unsatisfiable",
            Complete=True,
            Feasible=False,
        )
        for CurrentContract in CurrentContracts
    )

def _ProblemWithPhysicalPlan(Assembly, Plan):
    return replace(
        Assembly.Problem,
        PhysicalAssemblyPlan=Plan,
    )

def _MultiPortfolioFixture():
    Problem = _Assembly(_Problem()).Problem
    BasePort = Problem.Interface.PhysicalPortReservations[0]
    SourceDomain, TargetDomain = Problem.OwnedTerminalDomains

    def Alternate(Candidate, Name):
        Middle = (1, 7, 0)
        Path = (
            (Candidate.Attachment, Middle)
            if Candidate.Attachment != Middle
            else (Middle,)
        )
        return replace(
            Candidate,
            CandidateFingerprint=Name,
            Attachment=Middle,
            Path=Path,
            Claims=_Claims(Path),
        )

    AlternateSource = Alternate(
        SourceDomain.Candidates[0],
        "Alpha:alternate-source",
    )
    AlternateTarget = Alternate(
        TargetDomain.Candidates[0],
        "Alpha:alternate-target",
    )
    Problem = replace(
        Problem,
        OwnedTerminalDomains=(
            replace(
                SourceDomain,
                Candidates=(SourceDomain.Candidates[0], AlternateSource),
            ),
            replace(
                TargetDomain,
                Candidates=(TargetDomain.Candidates[0], AlternateTarget),
            ),
        ),
    )
    OriginalPort = replace(
        BasePort,
        OwnedAccessCandidates=(
            SourceDomain.Candidates[0],
            TargetDomain.Candidates[0],
        ),
        OwnedCandidateFingerprints=(
            SourceDomain.Candidates[0].CandidateFingerprint,
            TargetDomain.Candidates[0].CandidateFingerprint,
        ),
        ReservationFingerprint="original",
    )
    AlternatePort = replace(
        BasePort,
        OwnedAccessCandidates=(AlternateSource, AlternateTarget),
        OwnedCandidateFingerprints=(
            AlternateSource.CandidateFingerprint,
            AlternateTarget.CandidateFingerprint,
        ),
        ReservationFingerprint="alternate",
    )
    SupersetPort = replace(
        BasePort,
        OwnedAccessCandidates=(
            SourceDomain.Candidates[0],
            AlternateSource,
            TargetDomain.Candidates[0],
            AlternateTarget,
        ),
        OwnedCandidateFingerprints=tuple(sorted((
            SourceDomain.Candidates[0].CandidateFingerprint,
            AlternateSource.CandidateFingerprint,
            TargetDomain.Candidates[0].CandidateFingerprint,
            AlternateTarget.CandidateFingerprint,
        ))),
        ReservationFingerprint="superset",
    )
    return Problem, OriginalPort, AlternatePort, SupersetPort

def _ExactPortfolioForPort(Problem, Port):
    return CompileCompleteComponentNetVariantPortfolio(
        replace(
            Problem,
            Interface=replace(
                Problem.Interface,
                PhysicalPortReservations=(Port,),
            ),
        ),
        "Alpha",
        DeadlineSeconds=1.0,
    )

__all__ = tuple(Name for Name in globals() if not Name.startswith("__"))
