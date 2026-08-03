from dataclasses import replace
import json
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
import unittest

from Compiler.Routing.ComponentRouter import (
    ApplyRoutedComponentGlobalProfiles,
    BuildCoalescedComponentAccessCandidates,
    BuildClosedComponentInterface,
    BuildDeclaredComponentFeedthroughDomains,
    BuildComponentForeignTransitDomains,
    BuildComponentRoutingProblem,
    BuildComponentRoutingFabric,
    BuildComponentFabricAdjacency,
    BuildCompleteOpposingNetAccessRowContext,
    BuildCompleteOpposingNetAccessContractDomain,
    BuildOpposingNetEffectiveAccessSignature,
    BuildExactComponentPortRealizabilityContext,
    BuildExactComponentPortRealizabilityFingerprint,
    ClearStructuralPortRealizabilityCache,
    ComponentClaimsCompatibleForOwners,
    ComponentClaimsConflict,
    EvaluateCachedCompleteOpposingNetAccessPair,
    EvaluateCompleteOpposingNetAccessPair,
    EvaluateCompleteOpposingNetAccessContractRow,
    EvaluateExactComponentPortRealizability,
    FilterExternalSourcePoweredSeamCandidateDomains,
    FindCompleteComponentNetUnsatSubset,
    MaterializeRoutedComponentTemplate,
    PreserveRoutedComponentForeignEscapes,
    PrepareComponentSymbolicNetStateContext,
    PruneDominatedComponentAccessCandidates,
    SelectComponentIncidentSignals,
    SolveComponentRoutingProblem,
    SolveComponentRoutingProblemDynamic,
    CompilePreparedComponentSymbolicNetStates,
    ValidateRoutedComponentHandoff,
    _BuildCanonicalAccessCombinationKey,
    _BuildNetVariant,
    _PlanTreeRepeaters,
    _SolveComponentRoutingProblemLegacy,
)
from Compiler.Routing.ComponentPipeline import CompileClosedComponent
from Compiler.Routing.ComponentPipeline import (
    BuildPhysicalPortLocalContractFingerprint,
)
from Compiler.Routing.Models import (
    ClosedComponentInterface,
    ComponentFeedthroughContract,
    ComponentForeignTransitDomain,
    ComponentInterfacePort,
    ComponentRoutingFabric,
    ComponentRoutingProblem,
    ComponentTerminalAccessCandidate,
    ComponentTerminalAccessDomain,
    PhysicalComponentAssemblyPlan,
    PhysicalComponentPortReservation,
    RoutedComponentNet,
)
from Compiler.Routing.ResourceGraph import (
    LocalRouteClaim,
    PinAccessPortal,
    RoutingResourceGraph,
    RoutingResourceClaims,
)
from Compiler.Routing.ChannelPlanner import NetRoutingProfile
from Compiler.Placement.Geometry import PlacedDesign


def _Claims(*Nodes):
    Nodes = frozenset(Nodes)
    return RoutingResourceClaims(
        WireCells=Nodes,
        SupportCells=frozenset(
            (X, Y - 1, Z) for X, Y, Z in Nodes
        ),
        ElectricalCells=Nodes,
    )


def _Channel(*Lanes):
    return SimpleNamespace(
        PhysicalModel="test-tree-fabric",
        AffectedClusters=(0, 1),
        AffectedSignals=("Alpha",),
        Lanes=tuple(
            SimpleNamespace(
                Cells=tuple(Cells),
                IngressNodes=(Cells[0], Cells[-1]),
            )
            for Cells in Lanes
        ),
    )


def _Candidate(Path):
    return ComponentTerminalAccessCandidate(
        CandidateFingerprint=str(tuple(Path)),
        Attachment=Path[-1],
        Path=tuple(Path),
        Claims=_Claims(*Path),
    )


def test_tree_repeater_dp_reuses_unchanged_subtrees():
    Root = (0, 7, 0)
    SharedBranch = tuple((0, 7, Z) for Z in range(4))
    FirstBranch = tuple((X, 7, 0) for X in range(4))
    ExtendedBranch = (*FirstBranch, (4, 7, 0))

    def Tree(Branch):
        Nodes = frozenset((*SharedBranch, *Branch))
        Edges = frozenset(
            tuple(sorted((First, Second)))
            for Path in (SharedBranch, Branch)
            for First, Second in zip(Path, Path[1:])
        )
        return Nodes, Edges

    Cache = {}
    Statistics = {}
    FirstNodes, FirstEdges = Tree(FirstBranch)
    _PlanTreeRepeaters(
        FirstNodes,
        FirstEdges,
        Root,
        15,
        SubproblemCache=Cache,
        CacheStatistics=Statistics,
    )
    InitialHits = Statistics.get("HitCount", 0)
    SecondNodes, SecondEdges = Tree(ExtendedBranch)
    Cached = _PlanTreeRepeaters(
        SecondNodes,
        SecondEdges,
        Root,
        15,
        SubproblemCache=Cache,
        CacheStatistics=Statistics,
    )
    Uncached = _PlanTreeRepeaters(
        SecondNodes,
        SecondEdges,
        Root,
        15,
    )

    assert Cached == Uncached
    assert Statistics.get("HitCount", 0) > InitialHits


def _Portal(Signal, Terminal, Path, PortalId):
    Path = tuple(Path)
    return PinAccessPortal(
        PortalId=PortalId,
        Signal=Signal,
        Terminal=Terminal,
        Layer=0,
        Path=Path,
        Edges=frozenset(
            (First, Second) if First <= Second else (Second, First)
            for First, Second in zip(Path, Path[1:])
        ),
        Claims=_Claims(*Path),
        Length=len(Path),
        BendCount=0,
        ViaCount=0,
        Cost=0,
    )


def _Domain(Signal, Terminal, Role, *Candidates):
    return ComponentTerminalAccessDomain(
        Signal=Signal,
        Terminal=Terminal,
        TerminalRole=Role,
        TerminalFingerprint=f"{Role}-{Terminal}",
        Candidates=tuple(Candidates),
    )


def _Problem(
    Signal="Alpha",
    *,
    Fabric=None,
    Foreign=(),
    External=(),
    MaximumPowerDistance=15,
):
    Fabric = Fabric or BuildComponentRoutingFabric(
        _Channel(((0, 7, 0), (1, 7, 0), (2, 7, 0)))
    )
    Source = _Domain(
        Signal,
        (0, 7, 0),
        "source",
        _Candidate(((0, 7, 0),)),
    )
    Target = _Domain(
        Signal,
        (2, 7, 0),
        "target",
        _Candidate(((2, 7, 0),)),
    )
    return ComponentRoutingProblem(
        ProblemFingerprint="structural-problem",
        PlacementFingerprint="placement",
        LocalTemplateFingerprint="local",
        SelectedClusters=(0, 1),
        ComponentSignals=(Signal,),
        LocalClaims=(),
        Fabric=Fabric,
        OwnedTerminalDomains=(Source, Target),
        ExternalContinuationTerminals=tuple(External),
        ForeignEscapeDomains=tuple(Foreign),
        MaximumPowerDistance=MaximumPowerDistance,
        DomainComplete=True,
        MaximumWork=10_000,
    )


def _Net(Signal, Position):
    Claims = _Claims(Position)
    return RoutedComponentNet(
        Signal=Signal,
        Root=Position,
        Nodes=frozenset((Position,)),
        Edges=frozenset(),
        WireCells=frozenset((Position,)),
        SupportCells=Claims.SupportCells,
        Repeaters=(),
        Claims=Claims,
        CoveredTerminals=(Position,),
        ExportedPorts=(),
        NetFingerprint=f"{Signal}-{Position}",
    )


def test_complete_net_subset_proves_monotone_capacity_unsat():
    First = _Net("First", (0, 7, 0))
    Second = _Net("Second", (0, 7, 0))

    Core = FindCompleteComponentNetUnsatSubset({
        "First": (First,),
        "Second": (Second,),
    })

    assert set(Core) == {"First", "Second"}
    assert FindCompleteComponentNetUnsatSubset({
        "First": (First,),
        "Second": (_Net("Second", (20, 7, 20)),),
    }) == ()
    assert FindCompleteComponentNetUnsatSubset(
        {"First": (First,), "Second": (Second,)},
        Advance=lambda: False,
    ) is None


def _OpposingPairProblem():
    Base = _Problem("Current")
    CompleteDomain = _Domain(
        "Complete",
        (1, 7, 0),
        "source",
        _Candidate(((1, 7, 0),)),
    )

    def Port(Signal, Attachment):
        return SimpleNamespace(
            Signal=Signal,
            Direction="output",
            FabricAttachment=Attachment,
            Attachment=Attachment,
            LocalPath=(Attachment,),
            OwnedTerminals=(Attachment,),
            OwnedCandidateFingerprints=(),
            OwnedAccessCandidates=(),
            FabricDomainFingerprint="fabric-" + Signal,
            Capacity=1,
        )

    return replace(
        Base,
        ComponentSignals=("Current", "Complete"),
        OwnedTerminalDomains=(
            *Base.OwnedTerminalDomains,
            CompleteDomain,
        ),
        Interface=SimpleNamespace(PhysicalPortReservations=(
            Port("Current", (0, 7, 0)),
            Port("Complete", (1, 7, 0)),
        )),
    )


def test_opposing_pair_oracle_has_typed_complete_and_incomplete_results():
    Problem = _OpposingPairProblem()
    Ports = {
        Port.Signal: Port
        for Port in Problem.Interface.PhysicalPortReservations
    }
    CurrentContract = BuildPhysicalPortLocalContractFingerprint(
        Ports["Current"]
    )
    CompleteContract = BuildPhysicalPortLocalContractFingerprint(
        Ports["Complete"]
    )
    BlockedVariants = (_Net("Complete", (1, 7, 0)),)
    BlockedRowContext = BuildCompleteOpposingNetAccessRowContext(
        Problem,
        BlockedVariants,
        CurrentSignal="Current",
        CompleteSignal="Complete",
    )
    Blocked = EvaluateCompleteOpposingNetAccessPair(
        Problem,
        CurrentSignal="Current",
        CompleteSignal="Complete",
        CurrentLocalContractFingerprint=CurrentContract,
        CompleteLocalContractFingerprint=CompleteContract,
        CompleteVariants=BlockedVariants,
        CompleteVariantDomainComplete=True,
        DeadlineSeconds=1.0,
        DomainFingerprint="precomputed-pair-domain",
        RowContext=BlockedRowContext,
    )
    Supported = EvaluateCompleteOpposingNetAccessPair(
        Problem,
        CurrentSignal="Current",
        CompleteSignal="Complete",
        CurrentLocalContractFingerprint=CurrentContract,
        CompleteLocalContractFingerprint=CompleteContract,
        CompleteVariants=(_Net("Complete", (20, 7, 20)),),
        CompleteVariantDomainComplete=True,
        DeadlineSeconds=1.0,
    )
    Incomplete = EvaluateCompleteOpposingNetAccessPair(
        Problem,
        CurrentSignal="Current",
        CompleteSignal="Complete",
        CurrentLocalContractFingerprint=CurrentContract,
        CompleteLocalContractFingerprint=CompleteContract,
        CompleteVariants=(_Net("Complete", (1, 7, 0)),),
        CompleteVariantDomainComplete=False,
        DeadlineSeconds=1.0,
    )
    EmptyCompleteDomain = EvaluateCompleteOpposingNetAccessPair(
        Problem,
        CurrentSignal="Current",
        CompleteSignal="Complete",
        CurrentLocalContractFingerprint=CurrentContract,
        CompleteLocalContractFingerprint=CompleteContract,
        CompleteVariants=(),
        CompleteVariantDomainComplete=True,
        DeadlineSeconds=0.0,
    )

    assert (Blocked.Status, Blocked.Complete, Blocked.Feasible) == (
        "architectural-unsatisfiable",
        True,
        False,
    )
    assert Blocked.DomainFingerprint == "precomputed-pair-domain"
    assert Blocked.ProofFingerprint
    assert (Supported.Status, Supported.Complete, Supported.Feasible) == (
        "feasible",
        True,
        True,
    )
    assert Supported.SupportingCompleteVariantFingerprints
    assert (Incomplete.Status, Incomplete.Complete, Incomplete.Feasible) == (
        "incomplete",
        False,
        None,
    )
    assert not Incomplete.ProofFingerprint
    assert (
        EmptyCompleteDomain.Status,
        EmptyCompleteDomain.Complete,
        EmptyCompleteDomain.Feasible,
    ) == ("architectural-unsatisfiable", True, False)
    assert EmptyCompleteDomain.ProofFingerprint
    assert EmptyCompleteDomain.ExpansionCount == 0


def test_opposing_pair_oracle_rejects_mismatched_local_contract_identity():
    Problem = _OpposingPairProblem()
    Ports = {
        Port.Signal: Port
        for Port in Problem.Interface.PhysicalPortReservations
    }

    with unittest.TestCase().assertRaisesRegex(
        ValueError,
        "local contract fingerprint mismatch",
    ):
        EvaluateCompleteOpposingNetAccessPair(
            Problem,
            CurrentSignal="Current",
            CompleteSignal="Complete",
            CurrentLocalContractFingerprint="stale-current-contract",
            CompleteLocalContractFingerprint=(
                BuildPhysicalPortLocalContractFingerprint(Ports["Complete"])
            ),
            CompleteVariants=(_Net("Complete", (1, 7, 0)),),
            CompleteVariantDomainComplete=True,
            DeadlineSeconds=1.0,
        )


def test_bulk_opposing_pair_oracle_matches_scalar_and_keeps_exact_identity():
    Problem = _OpposingPairProblem()
    Ports = {
        Port.Signal: Port
        for Port in Problem.Interface.PhysicalPortReservations
    }
    First = Ports["Current"]
    Second = SimpleNamespace(
        **{
            **vars(First),
            "LocalPath": (*First.LocalPath, (0, 7, 1)),
        }
    )
    CurrentPorts = {
        BuildPhysicalPortLocalContractFingerprint(Port): Port
        for Port in (First, Second)
    }
    CompleteContract = BuildPhysicalPortLocalContractFingerprint(
        Ports["Complete"]
    )
    Variants = (
        _Net("Complete", (1, 7, 0)),
        _Net("Complete", (20, 7, 20)),
    )
    Context = BuildCompleteOpposingNetAccessRowContext(
        Problem,
        Variants,
        CurrentSignal="Current",
        CompleteSignal="Complete",
    )
    Domains = {
        Contract: "exact-domain:" + Contract
        for Contract in CurrentPorts
    }
    ContractDomain = BuildCompleteOpposingNetAccessContractDomain(
        Problem,
        "Current",
        CurrentPorts,
    )

    assert len(ContractDomain.CanonicalAccessSignatures) == 1
    assert set(dict(
        ContractDomain.SignatureIndexByCurrentContract
    ).values()) == {0}
    assert dict(ContractDomain.SignaturesByCurrentContract) == {
        Contract: BuildOpposingNetEffectiveAccessSignature(
            Problem,
            "Current",
            Port,
        )
        for Contract, Port in CurrentPorts.items()
    }

    Bulk = EvaluateCompleteOpposingNetAccessContractRow(
        Problem,
        CurrentSignal="Current",
        CompleteSignal="Complete",
        CurrentPortsByContract=CurrentPorts,
        CompleteLocalContractFingerprint=CompleteContract,
        CompleteVariants=Variants,
        CompleteVariantDomainComplete=True,
        DeadlineSeconds=1.0,
        DomainFingerprintsByCurrentContract=Domains,
        ContractDomain=ContractDomain,
        RowContext=Context,
    )

    assert Bulk.AccessSignatureCount == 1
    assert Bulk.VariantScanCount <= len(Variants)
    assert set(Bulk.Results) == set(CurrentPorts)
    assert len({Value.DomainFingerprint for Value in Bulk.Results.values()}) == 2
    assert len({Value.ProofFingerprint for Value in Bulk.Results.values()}) == 2
    for Contract, Port in CurrentPorts.items():
        ScalarProblem = replace(
            Problem,
            Interface=replace(
                Problem.Interface,
                PhysicalPortReservations=(Port, Ports["Complete"]),
            ) if hasattr(Problem.Interface, "__dataclass_fields__") else (
                SimpleNamespace(PhysicalPortReservations=(
                    Port,
                    Ports["Complete"],
                ))
            ),
        )
        Scalar = EvaluateCompleteOpposingNetAccessPair(
            ScalarProblem,
            CurrentSignal="Current",
            CompleteSignal="Complete",
            CurrentLocalContractFingerprint=Contract,
            CompleteLocalContractFingerprint=CompleteContract,
            CompleteVariants=Variants,
            CompleteVariantDomainComplete=True,
            DeadlineSeconds=1.0,
            DomainFingerprint=Domains[Contract],
            RowContext=Context,
        )
        BulkResult = Bulk.Results[Contract]
        assert (
            BulkResult.Status,
            BulkResult.Complete,
            BulkResult.Feasible,
            BulkResult.DomainFingerprint,
            BulkResult.ProofFingerprint,
            BulkResult.SupportingCompleteVariantFingerprints,
            BulkResult.ExpansionCount,
        ) == (
            Scalar.Status,
            Scalar.Complete,
            Scalar.Feasible,
            Scalar.DomainFingerprint,
            Scalar.ProofFingerprint,
            Scalar.SupportingCompleteVariantFingerprints,
            Scalar.ExpansionCount,
        )


def test_bulk_opposing_pair_reuses_precomputed_current_contract_domain(
    monkeypatch,
):
    Problem = _OpposingPairProblem()
    Ports = {
        Port.Signal: Port
        for Port in Problem.Interface.PhysicalPortReservations
    }
    CurrentContract = BuildPhysicalPortLocalContractFingerprint(
        Ports["Current"]
    )
    CompleteContract = BuildPhysicalPortLocalContractFingerprint(
        Ports["Complete"]
    )
    Variants = (_Net("Complete", (20, 7, 20)),)
    ContractDomain = BuildCompleteOpposingNetAccessContractDomain(
        Problem,
        "Current",
        {CurrentContract: Ports["Current"]},
    )
    Context = BuildCompleteOpposingNetAccessRowContext(
        Problem,
        Variants,
        CurrentSignal="Current",
        CompleteSignal="Complete",
    )

    monkeypatch.setattr(
        "Compiler.Routing.ComponentRouter."
        "BuildOpposingNetEffectiveAccessSignature",
        lambda *_Arguments, **_Keywords: (_ for _ in ()).throw(
            AssertionError("current access domain was recomputed")
        ),
    )
    Result = EvaluateCompleteOpposingNetAccessContractRow(
        Problem,
        CurrentSignal="Current",
        CompleteSignal="Complete",
        CurrentPortsByContract={CurrentContract: Ports["Current"]},
        CompleteLocalContractFingerprint=CompleteContract,
        CompleteVariants=Variants,
        CompleteVariantDomainComplete=True,
        DeadlineSeconds=1.0,
        DomainFingerprintsByCurrentContract={
            CurrentContract: "exact-domain"
        },
        ContractDomain=ContractDomain,
        RowContext=Context,
    ).Results[CurrentContract]

    def UnexpectedRevalidation(*_Arguments, **_Keywords):
        raise AssertionError("immutable access domain was revalidated")

    for Name in (
        "_OpposingNetResourceIdentityFingerprint",
        "_OpposingRowCurrentAccessDomainFingerprint",
        "_CanonicalOpposingNetAccessSignatureFingerprint",
        "_BuildOpposingNetEffectiveAccessSignatureFromDomains",
    ):
        monkeypatch.setattr(
            "Compiler.Routing.ComponentRouter." + Name,
            UnexpectedRevalidation,
        )
    WarmResult = EvaluateCompleteOpposingNetAccessContractRow(
        Problem,
        CurrentSignal="Current",
        CompleteSignal="Complete",
        CurrentPortsByContract={CurrentContract: Ports["Current"]},
        CompleteLocalContractFingerprint=CompleteContract,
        CompleteVariants=Variants,
        CompleteVariantDomainComplete=True,
        DeadlineSeconds=1.0,
        DomainFingerprintsByCurrentContract={
            CurrentContract: "exact-domain"
        },
        ContractDomain=ContractDomain,
        RowContext=Context,
    ).Results[CurrentContract]

    assert Result.Complete
    assert Result.Status in {"feasible", "architectural-unsatisfiable"}
    assert Result.DomainFingerprint == "exact-domain"
    assert WarmResult == Result


def test_bulk_opposing_pair_rejects_stale_current_contract_domain():
    Problem = _OpposingPairProblem()
    Ports = {
        Port.Signal: Port
        for Port in Problem.Interface.PhysicalPortReservations
    }
    CurrentContract = BuildPhysicalPortLocalContractFingerprint(
        Ports["Current"]
    )
    CompleteContract = BuildPhysicalPortLocalContractFingerprint(
        Ports["Complete"]
    )
    ContractDomain = BuildCompleteOpposingNetAccessContractDomain(
        Problem,
        "Current",
        {CurrentContract: Ports["Current"]},
    )
    Candidate = Problem.OwnedTerminalDomains[0].Candidates[0]
    ChangedProblem = replace(
        Problem,
        OwnedTerminalDomains=(
            replace(
                Problem.OwnedTerminalDomains[0],
                Candidates=(replace(
                    Candidate,
                    Claims=replace(
                        Candidate.Claims,
                        ElectricalCells=frozenset(((30, 7, 30),)),
                    ),
                ),),
            ),
            *Problem.OwnedTerminalDomains[1:],
        ),
    )
    with unittest.TestCase().assertRaisesRegex(
        ValueError,
        "contract domain identity mismatch",
    ):
        EvaluateCompleteOpposingNetAccessContractRow(
            ChangedProblem,
            CurrentSignal="Current",
            CompleteSignal="Complete",
            CurrentPortsByContract={CurrentContract: Ports["Current"]},
            CompleteLocalContractFingerprint=CompleteContract,
            CompleteVariants=(_Net("Complete", (20, 7, 20)),),
            CompleteVariantDomainComplete=True,
            DeadlineSeconds=1.0,
            DomainFingerprintsByCurrentContract={
                CurrentContract: "exact-domain"
            },
            ContractDomain=ContractDomain,
        )


def test_bulk_opposing_pair_rejects_corrupt_canonical_signature_index():
    Problem = _OpposingPairProblem()
    Ports = {
        Port.Signal: Port
        for Port in Problem.Interface.PhysicalPortReservations
    }
    CurrentContract = BuildPhysicalPortLocalContractFingerprint(
        Ports["Current"]
    )
    CompleteContract = BuildPhysicalPortLocalContractFingerprint(
        Ports["Complete"]
    )
    ContractDomain = BuildCompleteOpposingNetAccessContractDomain(
        Problem,
        "Current",
        {CurrentContract: Ports["Current"]},
    )
    SignatureFingerprint, Signature = (
        ContractDomain.CanonicalAccessSignatures[0]
    )
    CorruptDomain = replace(
        ContractDomain,
        CanonicalAccessSignatures=((
            "0" * len(SignatureFingerprint),
            Signature,
        ),),
    )

    with unittest.TestCase().assertRaisesRegex(
        ValueError,
        "contract domain identity mismatch",
    ):
        EvaluateCompleteOpposingNetAccessContractRow(
            Problem,
            CurrentSignal="Current",
            CompleteSignal="Complete",
            CurrentPortsByContract={CurrentContract: Ports["Current"]},
            CompleteLocalContractFingerprint=CompleteContract,
            CompleteVariants=(_Net("Complete", (20, 7, 20)),),
            CompleteVariantDomainComplete=True,
            DeadlineSeconds=1.0,
            DomainFingerprintsByCurrentContract={
                CurrentContract: "exact-domain"
            },
            ContractDomain=CorruptDomain,
        )


def test_bulk_access_signature_includes_attachment_and_every_claim_set():
    Problem = _OpposingPairProblem()
    CurrentPort = next(
        Port
        for Port in Problem.Interface.PhysicalPortReservations
        if Port.Signal == "Current"
    )
    BaseCandidate = Problem.OwnedTerminalDomains[0].Candidates[0]
    BaseSignature = BuildOpposingNetEffectiveAccessSignature(
        Problem,
        "Current",
        CurrentPort,
    )
    ClaimFields = (
        "WireCells",
        "SupportCells",
        "RequiredAirCells",
        "ElectricalCells",
    )
    for Index, FieldName in enumerate(ClaimFields):
        Claims = replace(
            BaseCandidate.Claims,
            **{FieldName: frozenset(((30 + Index, 7, 30),))},
        )
        Candidate = replace(BaseCandidate, Claims=Claims)
        Domain = replace(
            Problem.OwnedTerminalDomains[0],
            Candidates=(Candidate,),
        )
        Changed = replace(
            Problem,
            OwnedTerminalDomains=(
                Domain,
                *Problem.OwnedTerminalDomains[1:],
            ),
        )
        assert BuildOpposingNetEffectiveAccessSignature(
            Changed,
            "Current",
            CurrentPort,
        ) != BaseSignature
    ChangedAttachment = replace(
        BaseCandidate,
        Attachment=(30, 7, 30),
    )
    ChangedProblem = replace(
        Problem,
        OwnedTerminalDomains=(
            replace(
                Problem.OwnedTerminalDomains[0],
                Candidates=(ChangedAttachment,),
            ),
            *Problem.OwnedTerminalDomains[1:],
        ),
    )
    assert BuildOpposingNetEffectiveAccessSignature(
        ChangedProblem,
        "Current",
        CurrentPort,
    ) != BaseSignature


def test_bulk_opposing_pair_oracle_rejects_stale_row_context():
    Problem = _OpposingPairProblem()
    Ports = {
        Port.Signal: Port
        for Port in Problem.Interface.PhysicalPortReservations
    }
    CurrentContract = BuildPhysicalPortLocalContractFingerprint(
        Ports["Current"]
    )
    CompleteContract = BuildPhysicalPortLocalContractFingerprint(
        Ports["Complete"]
    )
    Context = BuildCompleteOpposingNetAccessRowContext(
        Problem,
        (_Net("Complete", (1, 7, 0)),),
        CurrentSignal="Current",
        CompleteSignal="Complete",
    )
    with unittest.TestCase().assertRaisesRegex(
        ValueError,
        "row context identity mismatch",
    ):
        EvaluateCompleteOpposingNetAccessContractRow(
            Problem,
            CurrentSignal="Current",
            CompleteSignal="Complete",
            CurrentPortsByContract={CurrentContract: Ports["Current"]},
            CompleteLocalContractFingerprint=CompleteContract,
            CompleteVariants=(_Net("Complete", (20, 7, 20)),),
            CompleteVariantDomainComplete=True,
            DeadlineSeconds=1.0,
            DomainFingerprintsByCurrentContract={
                CurrentContract: "exact-domain"
            },
            RowContext=Context,
        )


def test_bulk_opposing_pair_oracle_deadline_never_certifies_unresolved_pairs():
    Problem = _OpposingPairProblem()
    Ports = {
        Port.Signal: Port
        for Port in Problem.Interface.PhysicalPortReservations
    }
    CurrentContract = BuildPhysicalPortLocalContractFingerprint(
        Ports["Current"]
    )
    CompleteContract = BuildPhysicalPortLocalContractFingerprint(
        Ports["Complete"]
    )
    Result = EvaluateCompleteOpposingNetAccessContractRow(
        Problem,
        CurrentSignal="Current",
        CompleteSignal="Complete",
        CurrentPortsByContract={CurrentContract: Ports["Current"]},
        CompleteLocalContractFingerprint=CompleteContract,
        CompleteVariants=(_Net("Complete", (20, 7, 20)),),
        CompleteVariantDomainComplete=True,
        DeadlineSeconds=0.0,
        DomainFingerprintsByCurrentContract={
            CurrentContract: "exact-domain"
        },
    ).Results[CurrentContract]

    assert Result.Status == "incomplete"
    assert not Result.Complete
    assert Result.Feasible is None
    assert not Result.ProofFingerprint


def test_opposing_pair_oracle_identity_excludes_reserved_global_routes():
    Problem = _OpposingPairProblem()
    Ports = {
        Port.Signal: Port
        for Port in Problem.Interface.PhysicalPortReservations
    }
    WithGlobal = replace(
        Problem,
        ReservedGlobalClaimsBySignal=((
            "Foreign",
            _Claims((0, 7, 0), (1, 7, 0)),
        ),),
    )
    Arguments = dict(
        CurrentSignal="Current",
        CompleteSignal="Complete",
        CurrentLocalContractFingerprint=(
            BuildPhysicalPortLocalContractFingerprint(Ports["Current"])
        ),
        CompleteLocalContractFingerprint=(
            BuildPhysicalPortLocalContractFingerprint(Ports["Complete"])
        ),
        CompleteVariants=(_Net("Complete", (1, 7, 0)),),
        CompleteVariantDomainComplete=True,
        DeadlineSeconds=1.0,
    )

    Base = EvaluateCompleteOpposingNetAccessPair(Problem, **Arguments)
    Relaxed = EvaluateCompleteOpposingNetAccessPair(WithGlobal, **Arguments)

    assert Base.DomainFingerprint == Relaxed.DomainFingerprint
    assert Base.ProofFingerprint == Relaxed.ProofFingerprint


def test_cached_opposing_pair_oracle_requires_complete_portfolio_entry():
    Problem = _OpposingPairProblem()
    Ports = {
        Port.Signal: Port
        for Port in Problem.Interface.PhysicalPortReservations
    }
    CurrentContract = BuildPhysicalPortLocalContractFingerprint(
        Ports["Current"]
    )
    CompleteContract = BuildPhysicalPortLocalContractFingerprint(
        Ports["Complete"]
    )
    Missing = EvaluateCachedCompleteOpposingNetAccessPair(
        Problem,
        CurrentSignal="Current",
        CompleteSignal="Complete",
        CurrentLocalContractFingerprint=CurrentContract,
        CompleteLocalContractFingerprint=CompleteContract,
        VariantPortfolioCache={},
        DeadlineSeconds=1.0,
    )

    assert Missing.Status == "incomplete"
    assert not Missing.Complete
    assert Missing.Feasible is None

    CompleteVariant = _Net("Complete", (1, 7, 0))
    Cached = EvaluateCachedCompleteOpposingNetAccessPair(
        Problem,
        CurrentSignal="Current",
        CompleteSignal="Complete",
        CurrentLocalContractFingerprint=CurrentContract,
        CompleteLocalContractFingerprint=CompleteContract,
        VariantPortfolioCache={
            (Problem.ProblemFingerprint, "Complete"): (
                (CompleteVariant,),
                1,
                {},
                frozenset(),
                (0, 7, 0),
            ),
        },
        DeadlineSeconds=1.0,
    )

    assert Cached.Status == "architectural-unsatisfiable"
    assert Cached.Complete
    assert Cached.Feasible is False
    assert Cached.ProofFingerprint

    CachedEmpty = EvaluateCachedCompleteOpposingNetAccessPair(
        Problem,
        CurrentSignal="Current",
        CompleteSignal="Complete",
        CurrentLocalContractFingerprint=CurrentContract,
        CompleteLocalContractFingerprint=CompleteContract,
        VariantPortfolioCache={
            (Problem.ProblemFingerprint, "Complete"): (
                (),
                0,
                {},
                frozenset(),
                (0, 7, 0),
            ),
        },
        DeadlineSeconds=0.0,
    )

    assert CachedEmpty.Status == "architectural-unsatisfiable"
    assert CachedEmpty.Complete
    assert CachedEmpty.Feasible is False
    assert CachedEmpty.ProofFingerprint
    assert CachedEmpty.Detail == (
        "complete opposing-net variant domain is empty"
    )


def test_exact_port_realizability_uses_powered_local_net_primitive():
    Problem = _Problem(
        External=(("Alpha", (10, 7, 0), "target"),),
    )
    Domains = Problem.OwnedTerminalDomains
    Candidates = tuple(
        Domain.Candidates[0] for Domain in Domains
    )
    LocalPath = ((2, 7, 0), (3, 7, 0))
    Cache = {}

    First = EvaluateExactComponentPortRealizability(
        Problem,
        Signal="Alpha",
        Domains=Domains,
        Candidates=Candidates,
        LocalPath=LocalPath,
        RealizabilityCache=Cache,
    )
    Renamed = _Problem(
        "Renamed",
        External=(("Renamed", (10, 7, 0), "target"),),
    )
    RenamedDomains = Renamed.OwnedTerminalDomains
    Second = EvaluateExactComponentPortRealizability(
        Renamed,
        Signal="Renamed",
        Domains=RenamedDomains,
        Candidates=tuple(
            Domain.Candidates[0] for Domain in RenamedDomains
        ),
        LocalPath=LocalPath,
        RealizabilityCache=Cache,
    )

    assert First.Realizable
    assert First.NetFingerprint
    assert not First.Diagnostics["CacheHit"]
    assert Second.Realizable
    assert Second.ContractFingerprint == First.ContractFingerprint
    assert Second.Diagnostics["CacheHit"]
    assert (
        BuildExactComponentPortRealizabilityFingerprint(
            Problem,
            Signal="Alpha",
            Domains=Domains,
            Candidates=Candidates,
            LocalPath=LocalPath,
        )
        == First.ContractFingerprint
    )
    OwnChannelFingerprint = (
        BuildExactComponentPortRealizabilityFingerprint(
            Problem,
            Signal="Alpha",
            Domains=Domains,
            Candidates=Candidates,
            LocalPath=LocalPath,
            ReservedClaimsBySignal=((
                "Alpha",
                _Claims((30, 7, 0)),
            ),),
        )
    )
    ForeignChannelFingerprint = (
        BuildExactComponentPortRealizabilityFingerprint(
            Problem,
            Signal="Alpha",
            Domains=Domains,
            Candidates=Candidates,
            LocalPath=LocalPath,
            ReservedClaimsBySignal=((
                "Foreign",
                _Claims((30, 7, 0)),
            ),),
        )
    )
    assert OwnChannelFingerprint == First.ContractFingerprint
    assert ForeignChannelFingerprint != First.ContractFingerprint


def test_exact_port_realizability_reuses_structural_predicate_across_rename():
    ClearStructuralPortRealizabilityCache()
    FirstProblem = _Problem(
        External=(("Alpha", (10, 7, 0), "target"),),
    )
    FirstDomains = FirstProblem.OwnedTerminalDomains
    First = EvaluateExactComponentPortRealizability(
        FirstProblem,
        Signal="Alpha",
        Domains=FirstDomains,
        Candidates=tuple(
            Domain.Candidates[0] for Domain in FirstDomains
        ),
        LocalPath=((2, 7, 0), (3, 7, 0)),
        UseStructuralCache=True,
    )
    RenamedProblem = _Problem(
        "Renamed",
        External=(("Renamed", (10, 7, 0), "target"),),
    )
    RenamedDomains = RenamedProblem.OwnedTerminalDomains
    Second = EvaluateExactComponentPortRealizability(
        RenamedProblem,
        Signal="Renamed",
        Domains=RenamedDomains,
        Candidates=tuple(
            Domain.Candidates[0] for Domain in RenamedDomains
        ),
        LocalPath=((2, 7, 0), (3, 7, 0)),
        UseStructuralCache=True,
    )

    assert First.Realizable
    assert not First.Diagnostics["CacheHit"]
    assert Second.Realizable
    assert Second.ContractFingerprint == First.ContractFingerprint
    assert Second.Diagnostics["CacheHit"]
    assert Second.Diagnostics["CacheScope"] == "structural"
    ClearStructuralPortRealizabilityCache()


def test_exact_port_context_factorizes_candidate_identity_across_seams():
    Problem = _Problem(
        External=(("Alpha", (10, 7, 0), "target"),),
    )
    Domains = Problem.OwnedTerminalDomains
    Candidates = tuple(
        Domain.Candidates[0] for Domain in Domains
    )
    Context = BuildExactComponentPortRealizabilityContext(
        Problem,
        Signal="Alpha",
    )

    First = BuildExactComponentPortRealizabilityFingerprint(
        Problem,
        Signal="Alpha",
        Domains=Domains,
        Candidates=Candidates,
        LocalPath=((2, 7, 0), (3, 7, 0)),
        Context=Context,
    )
    Repeated = BuildExactComponentPortRealizabilityFingerprint(
        Problem,
        Signal="Alpha",
        Domains=Domains,
        Candidates=Candidates,
        LocalPath=((2, 7, 0), (3, 7, 0)),
        Context=Context,
    )
    OtherSeam = BuildExactComponentPortRealizabilityFingerprint(
        Problem,
        Signal="Alpha",
        Domains=Domains,
        Candidates=Candidates,
        LocalPath=((2, 7, 0), (2, 7, 1)),
        Context=Context,
    )

    assert First == Repeated
    assert OtherSeam != First
    assert len(Context.CandidateIdentityCache) == 1
    assert len(Context.LocalPathIdentityCache) == 2


def test_component_access_coalesces_adjacent_terminal_into_shared_trunk():
    class HorizontalResourceGraph:
        def BuildPrimitive(self, First, Second):
            return (
                object()
                if sum(
                    abs(First[Index] - Second[Index])
                    for Index in range(3)
                ) == 1
                else None
            )

        def BuildRouteClaims(self, Nodes):
            return _Claims(*Nodes)

    Trunk = _Candidate((
        (0, 1, 0),
        (0, 1, 1),
        (0, 1, 2),
    ))
    Independent = _Candidate((
        (2, 1, 0),
        (2, 1, 1),
        (2, 1, 2),
        (1, 1, 2),
        (0, 1, 2),
    ))

    Values = BuildCoalescedComponentAccessCandidates(
        Independent,
        (Trunk,),
        ResourceGraph=HorizontalResourceGraph(),
        ExistingNodes=frozenset(Trunk.Path),
    )

    assert Values
    assert Values[0].Path[:3] == (
        (2, 1, 0),
        (1, 1, 0),
        (0, 1, 0),
    )
    assert Values[0].Path[-1] == Trunk.Attachment
    assert Values[0].Attachment == Trunk.Attachment


def test_exact_port_realizability_reports_self_claim_conflict():
    class SelfConflictingResourceGraph:
        GraphVersion = "self-conflict-test"
        Technology = SimpleNamespace()

        def BuildRouteClaims(self, Nodes):
            Nodes = frozenset(Nodes)
            Claims = _Claims(*Nodes)
            if len(Nodes) < 3:
                return Claims
            First = min(Nodes)
            return replace(
                Claims,
                SupportCells=Claims.SupportCells | frozenset((First,)),
            )

        def BuildPrimitive(self, _First, _Second):
            return object()

    Problem = replace(
        _Problem(
            External=(("Alpha", (10, 7, 0), "target"),),
        ),
        ResourceGraph=SelfConflictingResourceGraph(),
    )
    Domains = Problem.OwnedTerminalDomains
    Result = EvaluateExactComponentPortRealizability(
        Problem,
        Signal="Alpha",
        Domains=Domains,
        Candidates=tuple(
            Domain.Candidates[0] for Domain in Domains
        ),
        LocalPath=((2, 7, 0), (3, 7, 0)),
    )

    assert not Result.Realizable
    assert Result.Diagnostics["RejectionCounts"] == {
        "self-claim-conflict": 1,
    }
    assert not Result.Diagnostics["ImmutableConflictSignals"]


def test_external_source_seam_filters_disconnected_and_conflicting_access():
    LeftIsland = (
        (0, 7, 0),
        (1, 7, 0),
        (2, 7, 0),
    )
    RightIsland = (
        (10, 7, 0),
        (11, 7, 0),
    )
    Fabric = BuildComponentRoutingFabric(
        _Channel(LeftIsland, RightIsland)
    )
    ConflictMarker = LeftIsland[0]

    class CandidateSensitiveResourceGraph:
        def BuildRouteClaims(self, Nodes):
            Nodes = frozenset(Nodes)
            Claims = _Claims(*Nodes)
            if ConflictMarker not in Nodes:
                return Claims
            return replace(
                Claims,
                RequiredAirCells=(
                    Claims.RequiredAirCells
                    | frozenset((ConflictMarker,))
                ),
            )

    Disconnected = replace(
        _Candidate((RightIsland[0],)),
        CandidateFingerprint="00-disconnected",
    )
    ConnectedButConflicting = replace(
        _Candidate((ConflictMarker,)),
        CandidateFingerprint="10-connected-self-conflicting",
    )
    Legal = replace(
        _Candidate((LeftIsland[1],)),
        CandidateFingerprint="20-legal",
    )
    Domain = _Domain(
        "Alpha",
        (20, 7, 0),
        "target",
        Disconnected,
        ConnectedButConflicting,
        Legal,
    )
    Problem = replace(
        _Problem(
            Fabric=Fabric,
            External=(("Alpha", (30, 7, 0), "source"),),
        ),
        OwnedTerminalDomains=(Domain,),
        ResourceGraph=CandidateSensitiveResourceGraph(),
    )
    LocalPath = (LeftIsland[-1], (3, 7, 0))

    assert FilterExternalSourcePoweredSeamCandidateDomains(
        Problem,
        "Alpha",
        (Domain,),
        ((Disconnected,),),
        LocalPath,
    ) == ((),)
    assert FilterExternalSourcePoweredSeamCandidateDomains(
        Problem,
        "Alpha",
        (Domain,),
        ((ConnectedButConflicting,),),
        LocalPath,
    ) == ((),)
    assert FilterExternalSourcePoweredSeamCandidateDomains(
        Problem,
        "Alpha",
        (Domain,),
        ((Disconnected, ConnectedButConflicting, Legal),),
        LocalPath,
    ) == ((Legal,),)

    FabricAdjacency = BuildComponentFabricAdjacency(Fabric)
    ParentCache = {}
    ClaimsCache = {}
    RepeaterCache = {}
    RepeaterStatistics = {}
    CachedKeywords = {
        "FabricAdjacency": FabricAdjacency,
        "FabricParentCache": ParentCache,
        "RouteClaimsCache": ClaimsCache,
        "TreeRepeaterSubproblemCache": RepeaterCache,
        "TreeRepeaterCacheStatistics": RepeaterStatistics,
    }
    FirstCached = FilterExternalSourcePoweredSeamCandidateDomains(
        Problem,
        "Alpha",
        (Domain,),
        ((Legal,),),
        LocalPath,
        **CachedKeywords,
    )
    InitialRepeaterHits = RepeaterStatistics.get("HitCount", 0)
    SecondCached = FilterExternalSourcePoweredSeamCandidateDomains(
        Problem,
        "Alpha",
        (Domain,),
        ((Legal,),),
        LocalPath,
        **CachedKeywords,
    )

    assert FirstCached == SecondCached == ((Legal,),)
    assert ParentCache and ClaimsCache and RepeaterCache
    assert RepeaterStatistics.get("HitCount", 0) > InitialRepeaterHits
    assert FilterExternalSourcePoweredSeamCandidateDomains(
        Problem,
        "Alpha",
        (Domain,),
        ((Legal, ConnectedButConflicting, Disconnected),),
        LocalPath,
    ) == ((Legal,),)


def test_exact_port_predicate_matches_single_net_local_compilation():
    Problem = _Problem(
        External=(("Alpha", (10, 7, 0), "target"),),
    )
    Domains = Problem.OwnedTerminalDomains
    Candidates = tuple(
        Domain.Candidates[0] for Domain in Domains
    )
    LocalPath = ((2, 7, 0), (3, 7, 0))
    Port = PhysicalComponentPortReservation(
        Signal="Alpha",
        Direction="output",
        OwnedTerminals=tuple(
            Domain.Terminal for Domain in Domains
        ),
        OwnedTerminalFingerprints=tuple(
            Domain.TerminalFingerprint for Domain in Domains
        ),
        OwnedCandidateFingerprints=tuple(
            Candidate.CandidateFingerprint
            for Candidate in Candidates
        ),
        FabricDomainFingerprint="single-fabric",
        FabricAttachment=LocalPath[0],
        Attachment=LocalPath[-1],
        LocalPath=LocalPath,
        GlobalPath=(LocalPath[-1], (4, 7, 0)),
        Claims=_Claims(
            *(
                Position
                for Candidate in Candidates
                for Position in Candidate.Path
            ),
            *LocalPath,
            (4, 7, 0),
        ),
        ReservationFingerprint="exact-port",
    )
    Interface = ClosedComponentInterface(
        InterfaceFingerprint="exact-single-net-interface",
        ComponentId=0,
        OwnedSignals=("Alpha",),
        Ports=(
            ComponentInterfacePort(
                Signal="Alpha",
                Direction="output",
                OwnedTerminals=Port.OwnedTerminals,
                ExternalTerminalCount=1,
            ),
        ),
        PhysicalPortReservations=(Port,),
    )
    ExactProblem = replace(
        Problem,
        Interface=Interface,
        OwnedTerminalDomains=tuple(
            replace(Domain, Candidates=(Candidate,))
            for Domain, Candidate in zip(Domains, Candidates)
        ),
    )

    Predicate = EvaluateExactComponentPortRealizability(
        ExactProblem,
        Signal="Alpha",
        Domains=ExactProblem.OwnedTerminalDomains,
        Candidates=Candidates,
        LocalPath=LocalPath,
    )
    Compilation = SolveComponentRoutingProblem(
        ExactProblem,
        DiscoveryVariantLimit=None,
    )

    assert Predicate.Realizable == Compilation.Feasible
    assert Predicate.Realizable
    assert Compilation.Template is not None
    assert (
        Predicate.NetFingerprint
        == Compilation.Template.Nets[0].NetFingerprint
    )
    PowerUnsatisfiable = replace(
        ExactProblem,
        ProblemFingerprint="exact-single-net-power-unsatisfiable",
        MaximumPowerDistance=0,
    )
    UnsatisfiablePredicate = (
        EvaluateExactComponentPortRealizability(
            PowerUnsatisfiable,
            Signal="Alpha",
            Domains=PowerUnsatisfiable.OwnedTerminalDomains,
            Candidates=Candidates,
            LocalPath=LocalPath,
        )
    )
    UnsatisfiableCompilation = SolveComponentRoutingProblem(
        PowerUnsatisfiable,
        DiscoveryVariantLimit=None,
    )

    assert (
        UnsatisfiablePredicate.Realizable
        == UnsatisfiableCompilation.Feasible
        == False
    )
    assert (
        UnsatisfiablePredicate.Diagnostics["RejectionCounts"]
        == {"power-or-tree-connectivity": 1}
    )


def test_tree_fabric_is_deterministic_and_cycle_is_incomplete():
    First = BuildComponentRoutingFabric(
        _Channel(((0, 7, 0), (1, 7, 0), (2, 7, 0)))
    )
    Reordered = BuildComponentRoutingFabric(
        _Channel(((2, 7, 0), (1, 7, 0), (0, 7, 0)))
    )
    assert First.Complete
    assert First.FabricFingerprint == Reordered.FabricFingerprint

    Cyclic = BuildComponentRoutingFabric(_Channel((
        (0, 7, 0),
        (1, 7, 0),
        (1, 7, 1),
        (0, 7, 1),
        (0, 7, 0),
    )))
    assert not Cyclic.Complete
    assert Cyclic.IncompleteReason == "unsupported-cyclic-fabric"


def test_foreign_transit_selection_is_structural_and_rename_invariant():
    Fabric = BuildComponentRoutingFabric(_Channel(
        tuple((X, 7, 0) for X in range(7)),
        tuple((X, 7, 6) for X in range(7)),
    ))
    Problem = _Problem(Fabric=Fabric)
    Profile = NetRoutingProfile(
        Signal="Foreign",
        Root=(-10, 1, 3),
        Targets=((16, 1, 3),),
        Span=26,
        Fanout=1,
        RetryCount=0,
        Criticality=0,
        IsTrunk=False,
        SourceAccessPath=((-10, 1, 3),),
        TargetAccessPaths={(16, 1, 3): ((16, 1, 3),)},
    )

    First = BuildComponentForeignTransitDomains(
        Problem,
        {"Foreign": Profile},
    )
    Renamed = BuildComponentForeignTransitDomains(
        Problem,
        {
            "Renamed": replace(
                Profile,
                Signal="Renamed",
            ),
        },
    )

    assert len(First) == 1
    assert First[0].PartitionAxis == "X"
    assert First[0].Candidates
    assert (
        First[0].PartitionFingerprint
        == Renamed[0].PartitionFingerprint
    )


def test_production_component_problem_has_closed_ownership():
    Channel = _Channel(
        ((0, 7, 0), (1, 7, 0), (2, 7, 0)),
    )
    Request = SimpleNamespace(
        Signal="Alpha",
        SourceCluster=0,
        TargetCluster=1,
        SourceTerminal=(0, 7, 0),
        TargetTerminals=((2, 7, 0),),
    )
    Placed = SimpleNamespace(
        InterClusterRoutingChannel=Channel,
        ClusterBoundaryLeaseRequests=(Request,),
        LocalRouteClaims=(),
        Module=SimpleNamespace(Gates=()),
    )
    Profiles = {
        "Alpha": NetRoutingProfile(
            Signal="Alpha",
            Root=(0, 7, 0),
            Targets=((2, 7, 0),),
            Span=2,
            Fanout=1,
            RetryCount=0,
            Criticality=0,
            IsTrunk=False,
            SourceAccessPath=((0, 7, 0),),
            TargetAccessPaths={(2, 7, 0): ((2, 7, 0),)},
        ),
        "Foreign": NetRoutingProfile(
            Signal="Foreign",
            Root=(1, 7, -2),
            Targets=((1, 7, 2),),
            Span=4,
            Fanout=1,
            RetryCount=0,
            Criticality=0,
            IsTrunk=False,
            SourceAccessPath=((1, 7, -2),),
            TargetAccessPaths={(1, 7, 2): ((1, 7, 2),)},
        ),
    }
    RawPortals = {
        ("Alpha", (0, 7, 0), 0): (
            _Portal(
                "Alpha",
                (0, 7, 0),
                ((0, 7, 0),),
                "alpha-source",
            ),
        ),
        ("Alpha", (2, 7, 0), 0): (
            _Portal(
                "Alpha",
                (2, 7, 0),
                ((2, 7, 0),),
                "alpha-target",
            ),
        ),
        ("Foreign", (1, 7, -2), 0): (
            _Portal(
                "Foreign",
                (1, 7, -2),
                ((1, 7, -2), (1, 7, 0)),
                "foreign-source",
            ),
        ),
    }

    Problem = BuildComponentRoutingProblem(
        Placed=Placed,
        Profiles=Profiles,
        RawPortals=RawPortals,
        PlacementFingerprint="placement",
        LocalTemplateFingerprint="local",
    )

    assert Problem.Interface is not None
    assert Problem.Interface.OwnedSignals == ("Alpha",)
    assert not Problem.ForeignEscapeDomains
    assert not Problem.ExternalContinuationDomains
    assert not Problem.ForeignTransitDomains
    assert {
        Domain.Signal for Domain in Problem.OwnedTerminalDomains
    } == {"Alpha"}


def test_component_problem_promotes_cluster_cut_to_explicit_port():
    Channel = _Channel(
        ((0, 7, 0), (1, 7, 0), (2, 7, 0)),
    )
    Channel.AffectedSignals = ()
    Request = SimpleNamespace(
        Signal="Cut",
        SourceCluster=8,
        TargetCluster=1,
        SourceTerminal=(-2, 7, 0),
        TargetTerminals=((2, 7, 0),),
    )
    Placed = SimpleNamespace(
        InterClusterRoutingChannel=Channel,
        ClusterBoundaryLeaseRequests=(Request,),
        LocalRouteClaims=(),
        Module=SimpleNamespace(Gates=()),
    )
    Profile = NetRoutingProfile(
        Signal="Cut",
        Root=(-2, 7, 0),
        Targets=((2, 7, 0),),
        Span=4,
        Fanout=1,
        RetryCount=0,
        Criticality=0,
        IsTrunk=False,
        SourceAccessPath=((-2, 7, 0),),
        TargetAccessPaths={(2, 7, 0): ((2, 7, 0),)},
    )
    Problem = BuildComponentRoutingProblem(
        Placed=Placed,
        Profiles={"Cut": Profile},
        RawPortals={
            ("Cut", (2, 7, 0), 0): (
                _Portal(
                    "Cut",
                    (2, 7, 0),
                    ((2, 7, 0),),
                    "cut-target",
                ),
            ),
        },
        PlacementFingerprint="placement",
        LocalTemplateFingerprint="local",
    )

    assert Problem.Interface is not None
    assert Problem.Interface.OwnedSignals == ("Cut",)
    assert tuple(
        (Port.Signal, Port.Direction, Port.OwnedTerminals)
        for Port in Problem.Interface.Ports
    ) == (("Cut", "input", ((2, 7, 0),)),)


def test_closed_component_rejects_undeclared_foreign_transit():
    Problem = _Problem()
    Foreign = _Net("Foreign", (1, 7, 0))
    Transit = ComponentForeignTransitDomain(
        Signal="Foreign",
        PartitionAxis="X",
        PartitionFingerprint="partition",
        Candidates=(Foreign,),
    )
    Interface = ClosedComponentInterface(
        InterfaceFingerprint="closed-interface",
        ComponentId=0,
        OwnedSignals=("Alpha",),
        Ports=(),
    )

    Result = SolveComponentRoutingProblem(replace(
        Problem,
        Interface=Interface,
        ForeignTransitDomains=(Transit,),
    ))

    assert Result.Status == "architectural-unsatisfiable"
    assert Result.Diagnostics["ImplicitForeignTransitDomainCount"] == 1
    assert "undeclared foreign transit" in Result.Detail


def test_explicit_feedthrough_contract_records_endpoints_and_capacity():
    Channel = _Channel(
        ((0, 7, 0), (1, 7, 0), (2, 7, 0)),
    )
    Channel.DeclaredFeedthroughSignals = ("Foreign",)
    Channel.ComponentId = 4
    Channel.InterfaceFingerprint = "logical-interface"
    Fabric = BuildComponentRoutingFabric(Channel)
    Profile = NetRoutingProfile(
        Signal="Alpha",
        Root=(0, 7, 0),
        Targets=((2, 7, 0),),
        Span=2,
        Fanout=1,
        RetryCount=0,
        Criticality=0,
        IsTrunk=False,
        SourceAccessPath=((0, 7, 0),),
        TargetAccessPaths={(2, 7, 0): ((2, 7, 0),)},
    )

    Interface = BuildClosedComponentInterface(
        Channel=Channel,
        Fabric=Fabric,
        Profiles={"Alpha": Profile},
        ComponentSignals=("Alpha",),
        ComponentPairs=(
            ("Alpha", (0, 7, 0)),
            ("Alpha", (2, 7, 0)),
        ),
    )

    assert Interface.ComponentId == 4
    assert Interface.DeclaredFeedthroughSignals == {"Foreign"}
    assert Interface.Feedthroughs == (
        ComponentFeedthroughContract(
            Signal="Foreign",
            EndpointPairs=(((0, 7, 0), (2, 7, 0)),),
            Capacity=1,
        ),
    )


def test_declared_feedthrough_compiles_only_its_exact_endpoints():
    Fabric = BuildComponentRoutingFabric(_Channel(
        tuple((X, 7, 0) for X in range(7)),
    ))
    Problem = _Problem(Fabric=Fabric)
    Contract = ComponentFeedthroughContract(
        Signal="Foreign",
        EndpointPairs=(((0, 7, 0), (6, 7, 0)),),
        Capacity=1,
    )

    Domains = BuildDeclaredComponentFeedthroughDomains(
        Problem,
        (Contract,),
    )

    assert len(Domains) == 1
    assert Domains[0].Complete
    assert Domains[0].Diagnostics["Mode"] == "declared-feedthrough"
    assert Domains[0].Candidates
    assert all(
        Candidate.Nodes <= frozenset(Fabric.Nodes)
        and (0, 7, 0) in Candidate.Nodes
        and (6, 7, 0) in Candidate.Nodes
        for Candidate in Domains[0].Candidates
    )


def test_declared_physical_feedthrough_compiles_only_reserved_path():
    Fabric = BuildComponentRoutingFabric(_Channel(
        tuple((X, 7, 0) for X in range(5)),
        tuple((X, 7, 1) for X in range(5)),
    ))
    Problem = _Problem(Fabric=Fabric)
    ReservedPath = tuple((X, 7, 1) for X in range(5))
    Contract = ComponentFeedthroughContract(
        Signal="Foreign",
        EndpointPairs=((ReservedPath[0], ReservedPath[-1]),),
        Capacity=1,
        ReservedPathNodes=ReservedPath,
        ReservationFingerprint="physical-feedthrough",
    )

    (Domain,) = BuildDeclaredComponentFeedthroughDomains(
        Problem,
        (Contract,),
    )

    assert Domain.Complete
    assert len(Domain.Candidates) == 1
    assert Domain.Candidates[0].Nodes == frozenset(ReservedPath)


def test_declared_physical_feedthrough_rejects_reserved_path_drift():
    Fabric = BuildComponentRoutingFabric(_Channel(
        tuple((X, 7, 0) for X in range(5)),
    ))
    Problem = _Problem(Fabric=Fabric)
    Contract = ComponentFeedthroughContract(
        Signal="Foreign",
        EndpointPairs=(((0, 7, 0), (4, 7, 0)),),
        ReservedPathNodes=((0, 7, 0), (2, 7, 0), (4, 7, 0)),
        ReservationFingerprint="disconnected-feedthrough",
    )

    (Domain,) = BuildDeclaredComponentFeedthroughDomains(
        Problem,
        (Contract,),
    )

    assert Domain.Complete
    assert not Domain.Candidates
    assert Domain.Diagnostics["RejectionCounts"] == {
        "invalid-declared-endpoints": 1,
    }


def test_foreign_transit_competes_for_capacity_and_freezes_as_seed():
    Fabric = BuildComponentRoutingFabric(_Channel(
        tuple((X, 7, 0) for X in range(7)),
        tuple((X, 7, 6) for X in range(7)),
    ))
    Problem = _Problem(Fabric=Fabric)
    Profile = NetRoutingProfile(
        Signal="Foreign",
        Root=(-10, 1, 3),
        Targets=((16, 1, 3),),
        Span=26,
        Fanout=1,
        RetryCount=0,
        Criticality=0,
        IsTrunk=False,
        SourceAccessPath=((-10, 1, 3),),
        TargetAccessPaths={(16, 1, 3): ((16, 1, 3),)},
    )
    TransitDomains = BuildComponentForeignTransitDomains(
        Problem,
        {"Foreign": Profile},
    )
    OptionalResult = SolveComponentRoutingProblem(replace(
        Problem,
        ForeignTransitDomains=TransitDomains,
    ))
    assert OptionalResult.Feasible
    assert OptionalResult.Template is not None
    assert not OptionalResult.Template.ForeignTransitReservations
    Result = SolveComponentRoutingProblem(
        replace(
            Problem,
            ForeignTransitDomains=TransitDomains,
        ),
        RequiredForeignTransitSignals=frozenset(("Foreign",)),
    )

    assert Result.Feasible
    assert Result.Template is not None
    assert len(Result.Template.ForeignTransitReservations) == 1
    Placed = PlacedDesign(
        Module=SimpleNamespace(),
        PlacedGates=[],
        LocalRouteClaims=(),
        LocalRouteDiagnostics={},
        RoutedComponentTemplates=(),
    )
    Materialized = MaterializeRoutedComponentTemplate(
        Placed,
        Result.Template,
    )
    TransitClaims = tuple(
        Claim
        for Claim in Materialized.LocalRouteClaims
        if Claim.ClusterId == -4
    )
    assert len(TransitClaims) == 1
    assert TransitClaims[0].Signal == "Foreign"
    assert len(TransitClaims[0].BoundaryNodes) == 2


def test_required_foreign_transit_pair_is_prechecked_before_net_search():
    First = _Net("FirstTransit", (20, 7, 20))
    Second = _Net("SecondTransit", (20, 7, 20))
    Domains = (
        ComponentForeignTransitDomain(
            Signal=First.Signal,
            PartitionAxis="X",
            PartitionFingerprint="first",
            Candidates=(First,),
        ),
        ComponentForeignTransitDomain(
            Signal=Second.Signal,
            PartitionAxis="Z",
            PartitionFingerprint="second",
            Candidates=(Second,),
        ),
    )
    Result = SolveComponentRoutingProblem(
        replace(
            _Problem(),
            ForeignTransitDomains=Domains,
        ),
        RequiredForeignTransitSignals=frozenset((
            First.Signal,
            Second.Signal,
        )),
    )

    assert Result.Status == "architectural-unsatisfiable"
    assert Result.ExpansionCount == 0
    Precheck = Result.Diagnostics["RequiredTransitPrecheck"]
    assert Precheck["Complete"]
    assert (
        Precheck["PairCompatibility"][0][
            "CompatiblePairCount"
        ]
        == 0
    )


def test_foreign_boundary_parallel_continuation_uses_dominant_span():
    Fabric = BuildComponentRoutingFabric(_Channel(
        tuple((X, 7, 0) for X in range(7)),
        tuple((0, 7, Z) for Z in range(1, 7)),
    ))
    Problem = _Problem(Fabric=Fabric)
    Profile = NetRoutingProfile(
        Signal="BoundaryForeign",
        Root=(-8, 1, -10),
        Targets=((-7, 1, 16),),
        Span=27,
        Fanout=1,
        RetryCount=0,
        Criticality=0,
        IsTrunk=False,
        SourceAccessPath=((-8, 1, -10),),
        TargetAccessPaths={
            (-7, 1, 16): ((-7, 1, 16),),
        },
    )

    Domains = BuildComponentForeignTransitDomains(
        Problem,
        {"BoundaryForeign": Profile},
    )

    assert len(Domains) == 1
    assert Domains[0].PartitionAxis == "Z"
    assert Domains[0].Candidates


def test_access_candidate_dominance_preserves_distinct_attachments():
    Compact = _Candidate(((0, 7, 0),))
    Larger = ComponentTerminalAccessCandidate(
        CandidateFingerprint="larger",
        Attachment=Compact.Attachment,
        Path=((0, 7, 0), (0, 7, 1)),
        Claims=_Claims((0, 7, 0), (0, 7, 1)),
    )
    Distinct = _Candidate(((1, 7, 0),))

    Retained = PruneDominatedComponentAccessCandidates((
        Larger,
        Distinct,
        Compact,
    ))

    assert Compact in Retained
    assert Distinct in Retained
    assert Larger not in Retained


def test_component_signal_selection_uses_cluster_incidence():
    Requests = (
        SimpleNamespace(
            Signal="Internal",
            SourceCluster=1,
            TargetCluster=2,
        ),
        SimpleNamespace(
            Signal="Entering",
            SourceCluster=8,
            TargetCluster=2,
        ),
        SimpleNamespace(
            Signal="Leaving",
            SourceCluster=1,
            TargetCluster=9,
        ),
        SimpleNamespace(
            Signal="Unrelated",
            SourceCluster=8,
            TargetCluster=9,
        ),
    )
    assert SelectComponentIncidentSignals(
        Requests,
        (1, 2),
        ("Internal", "Entering", "Leaving", "Unrelated"),
    ) == frozenset({"Internal", "Entering", "Leaving"})
    Renamed = tuple(
        SimpleNamespace(
            Signal=f"Signal{Index}",
            SourceCluster=Request.SourceCluster,
            TargetCluster=Request.TargetCluster,
        )
        for Index, Request in enumerate(reversed(Requests))
    )
    assert len(SelectComponentIncidentSignals(
        Renamed,
        (2, 1),
        tuple(Request.Signal for Request in Renamed),
    )) == 3


def test_unique_subtree_routes_and_detects_exact_capacity_conflict():
    Result = SolveComponentRoutingProblem(_Problem())
    assert Result.Feasible
    assert Result.Template is not None
    assert Result.Template.Nets[0].Nodes == frozenset({
        (0, 7, 0),
        (1, 7, 0),
        (2, 7, 0),
    })
    assert ComponentClaimsConflict(
        _Claims((0, 7, 0)),
        _Claims((0, 7, 0)),
    )
    assert not ComponentClaimsConflict(
        RoutingResourceClaims(
            SupportCells=frozenset({(0, 6, 0)}),
        ),
        RoutingResourceClaims(
            SupportCells=frozenset({(0, 6, 0)}),
        ),
    )
    assert ComponentClaimsConflict(
        RoutingResourceClaims(
            SupportCells=frozenset({(0, 6, 0)}),
        ),
        RoutingResourceClaims(
            WireCells=frozenset({(0, 6, 0)}),
        ),
    )


def test_unique_subtree_conflict_fixture_is_exhaustive():
    Base = _Problem()
    Conflicting = ComponentRoutingProblem(
        **{
            **Base.__dict__,
            "ComponentSignals": ("Alpha", "Beta"),
            "OwnedTerminalDomains": (
                *Base.OwnedTerminalDomains,
                _Domain(
                    "Beta",
                    (0, 7, 0),
                    "source",
                    _Candidate(((0, 7, 0),)),
                ),
                _Domain(
                    "Beta",
                    (2, 7, 0),
                    "target",
                    _Candidate(((2, 7, 0),)),
                ),
            ),
        }
    )
    Bounded = SolveComponentRoutingProblem(
        Conflicting,
        DiscoveryVariantLimit=1,
    )
    assert Bounded.Status == "incomplete"
    assert Bounded.Diagnostics["CapacityEmptyDomainWitnesses"]
    Result = SolveComponentRoutingProblem(Conflicting)
    assert Result.Exhaustive


def test_tree_frontier_dp_matches_legacy_feasible_template():
    Problem = _Problem()
    Legacy = _SolveComponentRoutingProblemLegacy(
        Problem,
        DiscoveryVariantLimit=None,
    )
    Dynamic = SolveComponentRoutingProblemDynamic(Problem)

    assert Legacy.Feasible and Dynamic.Feasible
    assert Legacy.Template is not None and Dynamic.Template is not None
    assert (
        Legacy.Template.RoutedTemplateFingerprint
        == Dynamic.Template.RoutedTemplateFingerprint
    )
    assert Dynamic.Diagnostics["SolverKind"] == "tree-frontier-dp-v1"
    assert Dynamic.Diagnostics["CompleteTreesMaterialized"] == 0
    assert Dynamic.Diagnostics["SelectedTreesMaterialized"] == 1


def test_prepared_symbolic_net_state_context_matches_dynamic_solver():
    Problem = _Problem()
    DynamicCache = {}
    Dynamic = SolveComponentRoutingProblemDynamic(
        Problem,
        SymbolicNetStateCache=DynamicCache,
        RequestedSymbolicStateSignals=frozenset(("Alpha",)),
        StopAfterOwnedSignalFrontierProof=True,
    )
    DynamicStates = next(iter(DynamicCache.values()))[0]

    PreparedCache = {}
    Context = PrepareComponentSymbolicNetStateContext(
        Problem,
        "Alpha",
    )
    Prepared = CompilePreparedComponentSymbolicNetStates(
        Context,
        Problem,
        SymbolicNetStateCache=PreparedCache,
    )

    assert Dynamic.Status == "frontier-feasible"
    assert Prepared.Complete
    assert not Prepared.CacheHit
    assert Prepared.States == DynamicStates
    assert tuple(State.NetFingerprint for State in Prepared.States) == tuple(
        State.NetFingerprint for State in DynamicStates
    )
    assert Prepared.CacheKey in PreparedCache


def test_prepared_symbolic_net_state_context_reuses_exact_access_cache():
    Problem = _Problem()
    RouteClaimsCache = {}
    StateCache = {}
    Context = PrepareComponentSymbolicNetStateContext(
        Problem,
        "Alpha",
        RouteClaimsConstructionCache=RouteClaimsCache,
    )

    First = CompilePreparedComponentSymbolicNetStates(
        Context,
        Problem,
        SymbolicNetStateCache=StateCache,
    )
    ParentCacheSize = len(Context.FabricParentCache)
    RouteClaimsCacheSize = len(RouteClaimsCache)
    Second = CompilePreparedComponentSymbolicNetStates(
        Context,
        Problem,
        SymbolicNetStateCache=StateCache,
    )

    assert First.Complete and Second.Complete
    assert not First.CacheHit and Second.CacheHit
    assert Second.ExpansionCount == 0
    assert Second.CacheKey == First.CacheKey
    assert Second.States == First.States
    assert len(StateCache) == 1
    assert len(Context.FabricParentCache) == ParentCacheSize
    assert len(RouteClaimsCache) == RouteClaimsCacheSize


def test_prepared_symbolic_context_reuses_terminal_frontier_across_egresses():
    Base = _Problem(
        External=(("Alpha", (3, 7, 0), "target"),),
    )
    Certified = tuple(
        Candidate.CandidateFingerprint
        for Domain in Base.OwnedTerminalDomains
        for Candidate in Domain.Candidates
    )

    def WithEgress(LocalPath):
        Port = SimpleNamespace(
            Signal="Alpha",
            Direction="output",
            FabricDomainFingerprint="fabric-alpha",
            FabricAttachment=LocalPath[0],
            Attachment=LocalPath[-1],
            LocalPath=tuple(LocalPath),
            OwnedTerminals=tuple(
                Domain.Terminal for Domain in Base.OwnedTerminalDomains
            ),
            OwnedTerminalFingerprints=(),
            OwnedCandidateFingerprints=Certified,
            OwnedAccessCandidates=(),
            Capacity=1,
        )
        return replace(
            Base,
            Interface=SimpleNamespace(
                Complete=True,
                DeclaredFeedthroughSignals=frozenset(),
                PhysicalPortReservations=(Port,),
            ),
        )

    FirstProblem = WithEgress(((2, 7, 0), (3, 7, 0)))
    SecondProblem = WithEgress(((2, 7, 0), (2, 7, 1)))
    Context = PrepareComponentSymbolicNetStateContext(
        FirstProblem,
        "Alpha",
    )
    SharedStateCache = {}

    First = CompilePreparedComponentSymbolicNetStates(
        Context,
        FirstProblem,
        SymbolicNetStateCache=SharedStateCache,
    )
    Second = CompilePreparedComponentSymbolicNetStates(
        Context,
        SecondProblem,
        SymbolicNetStateCache=SharedStateCache,
    )
    ColdContext = PrepareComponentSymbolicNetStateContext(
        SecondProblem,
        "Alpha",
    )
    ColdSecond = CompilePreparedComponentSymbolicNetStates(
        ColdContext,
        SecondProblem,
        SymbolicNetStateCache={},
    )

    assert First.Complete and Second.Complete and ColdSecond.Complete
    assert First.States and Second.States
    assert First.States[0].EgressPath != Second.States[0].EgressPath
    assert Second.States == ColdSecond.States
    assert Context.TerminalFrontierBuildCount == 1
    assert Context.TerminalFrontierCacheHitCount == 1
    assert len(Context.TerminalFrontierCache) == 1
    assert Second.Diagnostics["TerminalFrontierCacheHit"] is True
    assert ColdSecond.Diagnostics["TerminalFrontierCacheHit"] is False


def test_tree_frontier_dp_matches_legacy_capacity_unsat():
    Base = _Problem()
    Conflicting = replace(
        Base,
        ComponentSignals=("Alpha", "Beta"),
        OwnedTerminalDomains=(
            *Base.OwnedTerminalDomains,
            _Domain(
                "Beta",
                (0, 7, 0),
                "source",
                _Candidate(((0, 7, 0),)),
            ),
            _Domain(
                "Beta",
                (2, 7, 0),
                "target",
                _Candidate(((2, 7, 0),)),
            ),
        ),
    )
    Legacy = _SolveComponentRoutingProblemLegacy(
        Conflicting,
        DiscoveryVariantLimit=None,
    )
    Dynamic = SolveComponentRoutingProblemDynamic(Conflicting)

    assert Legacy.Exhaustive and Dynamic.Exhaustive
    assert Dynamic.Diagnostics["CompleteTreesMaterialized"] == 0


def _OwnedFrontierEmptyProblem(*, RestrictedByPort: bool):
    Left = ((0, 7, 0), (1, 7, 0))
    Right = ((10, 7, 0), (11, 7, 0))
    Fabric = BuildComponentRoutingFabric(_Channel(Left, Right))
    SourceLeft = replace(
        _Candidate((Left[0],)),
        CandidateFingerprint="source-left",
    )
    SourceRight = replace(
        _Candidate((Right[0],)),
        CandidateFingerprint="source-right",
    )
    TargetLeft = replace(
        _Candidate((Left[-1],)),
        CandidateFingerprint="target-left",
    )
    SourceCandidates = (
        (SourceLeft, SourceRight)
        if RestrictedByPort
        else (SourceRight,)
    )
    CertifiedCandidates = (
        ("source-right", "target-left")
        if RestrictedByPort
        else ()
    )
    Port = SimpleNamespace(
        Signal="Alpha",
        Direction="output",
        FabricDomainFingerprint="fabric-alpha",
        FabricAttachment=Right[0],
        Attachment=Right[0],
        LocalPath=(Right[0],),
        OwnedTerminals=(Left[0], Left[-1]),
        OwnedTerminalFingerprints=(),
        OwnedCandidateFingerprints=CertifiedCandidates,
        OwnedAccessCandidates=(),
        Capacity=1,
    )
    return replace(
        _Problem(),
        Fabric=Fabric,
        OwnedTerminalDomains=(
            _Domain("Alpha", Left[0], "source", *SourceCandidates),
            _Domain("Alpha", Left[-1], "target", TargetLeft),
        ),
        Interface=SimpleNamespace(
            Complete=True,
            DeclaredFeedthroughSignals=frozenset(),
            PhysicalPortReservations=(Port,),
        ),
    )


def test_tree_frontier_restricted_candidate_failure_is_not_port_independent():
    Result = SolveComponentRoutingProblemDynamic(
        _OwnedFrontierEmptyProblem(RestrictedByPort=True)
    )

    assert Result.Status == "architectural-unsatisfiable"
    assert Result.Diagnostics["LocalUnsatCoreKind"] == (
        "tree-frontier-empty-signal"
    )
    Signal = Result.Diagnostics["SignalDiagnostics"]["Alpha"]
    assert Signal["EmptyPhase"] == "owned-terminal-frontier"
    assert Signal["CertifiedRejectedCandidateCount"] == 1
    assert Signal["OwnedSignalDomainContractIndependent"] is False


def test_tree_frontier_unfiltered_empty_domain_is_port_independent():
    Result = SolveComponentRoutingProblemDynamic(
        _OwnedFrontierEmptyProblem(RestrictedByPort=False)
    )

    assert Result.Status == "architectural-unsatisfiable"
    assert Result.Diagnostics["LocalUnsatCoreKind"] == (
        "tree-frontier-empty-owned-signal-domain"
    )
    assert Result.Diagnostics["LocalUnsatCoreProjectionFingerprint"]
    Signal = Result.Diagnostics["SignalDiagnostics"]["Alpha"]
    assert Signal["EmptyPhase"] == "owned-terminal-frontier"
    assert Signal["CertifiedRejectedCandidateCount"] == 0
    assert Signal["OwnedSignalDomainContractIndependent"] is True


def test_prepared_symbolic_context_caches_complete_empty_terminal_frontier():
    FirstProblem = _OwnedFrontierEmptyProblem(RestrictedByPort=True)
    FirstPort = FirstProblem.Interface.PhysicalPortReservations[0]
    SecondProblem = replace(
        FirstProblem,
        Interface=SimpleNamespace(
            **{
                **FirstProblem.Interface.__dict__,
                "PhysicalPortReservations": (
                    SimpleNamespace(
                        **{
                            **FirstPort.__dict__,
                            "LocalPath": (
                                FirstPort.LocalPath[0],
                                (FirstPort.LocalPath[0][0], 7, 1),
                            ),
                        }
                    ),
                ),
            }
        ),
    )
    Context = PrepareComponentSymbolicNetStateContext(
        FirstProblem,
        "Alpha",
    )

    First = CompilePreparedComponentSymbolicNetStates(
        Context,
        FirstProblem,
        SymbolicNetStateCache={},
    )
    Second = CompilePreparedComponentSymbolicNetStates(
        Context,
        SecondProblem,
        SymbolicNetStateCache={},
    )

    assert First.Complete and Second.Complete
    assert First.States == Second.States == ()
    assert Context.TerminalFrontierBuildCount == 1
    assert Context.TerminalFrontierCacheHitCount == 1
    assert len(Context.TerminalFrontierCache) == 1
    assert Second.Diagnostics["TerminalFrontierCacheHit"] is True


def test_unbound_owned_frontier_proof_uses_tree_dp_without_port_contract():
    Bound = _OwnedFrontierEmptyProblem(RestrictedByPort=False)
    Unbound = replace(
        Bound,
        PhysicalAssemblyPlan=None,
        Interface=SimpleNamespace(
            **{
                **Bound.Interface.__dict__,
                "PhysicalPortReservations": (),
            }
        ),
        ReservedGlobalClaimsBySignal=(),
    )

    Result = SolveComponentRoutingProblem(
        Unbound,
        StopAfterOwnedSignalFrontierProof=True,
    )

    assert Result.Status == "architectural-unsatisfiable"
    assert Result.Diagnostics["SolverKind"] == "tree-frontier-dp-v1"
    assert Result.Diagnostics["LocalUnsatCoreKind"] == (
        "tree-frontier-empty-owned-signal-domain"
    )
    assert Result.Diagnostics["LocalUnsatCoreProjectionFingerprint"]


def test_tree_frontier_dp_is_deterministic_and_typed_incomplete():
    First = SolveComponentRoutingProblemDynamic(_Problem())
    Second = SolveComponentRoutingProblemDynamic(_Problem())
    Incomplete = SolveComponentRoutingProblemDynamic(
        replace(_Problem(), MaximumWork=0)
    )

    assert First.Template is not None and Second.Template is not None
    assert (
        First.Template.RoutedTemplateFingerprint
        == Second.Template.RoutedTemplateFingerprint
    )
    assert First.Diagnostics["PeakFrontierStateCount"] == 1
    assert Incomplete.Status == "incomplete"
    assert Incomplete.Diagnostics["CompleteTreesMaterialized"] == 0


def _LoadCla4TreeDpFixture():
    Data = json.loads(
        (
            Path(__file__).parent
            / "Fixtures"
            / "Cla4ComponentTreeDpProblem.json"
        ).read_text(encoding="utf-8")
    )

    def Position(Value):
        return tuple(map(int, Value))

    def Claims(Value):
        return RoutingResourceClaims(
            WireCells=frozenset(map(Position, Value["WireCells"])),
            SupportCells=frozenset(map(
                Position,
                Value["SupportCells"],
            )),
            RequiredAirCells=frozenset(map(
                Position,
                Value["RequiredAirCells"],
            )),
            ElectricalCells=frozenset(map(
                Position,
                Value["ElectricalCells"],
            )),
        )

    def LocalClaim(Value):
        return LocalRouteClaim(
            Signal=Value["Signal"],
            ClusterId=int(Value["ClusterId"]),
            Root=Position(Value["Root"]),
            ConnectedTargets=tuple(map(
                Position,
                Value["ConnectedTargets"],
            )),
            BoundaryNodes=tuple(map(
                Position,
                Value["BoundaryNodes"],
            )),
            Nodes=frozenset(map(Position, Value["Nodes"])),
            Edges=frozenset(
                tuple(sorted((Position(First), Position(Second))))
                for First, Second in Value["Edges"]
            ),
            Claims=Claims(Value["Claims"]),
        )

    FabricValue = Data["Fabric"]
    Fabric = ComponentRoutingFabric(
        FabricFingerprint=FabricValue["FabricFingerprint"],
        Nodes=tuple(map(Position, FabricValue["Nodes"])),
        Edges=tuple(
            tuple(sorted((Position(First), Position(Second))))
            for First, Second in FabricValue["Edges"]
        ),
        IngressNodes=tuple(map(
            Position,
            FabricValue["IngressNodes"],
        )),
        TopologyKind=FabricValue["TopologyKind"],
        Complete=bool(FabricValue["Complete"]),
        IncompleteReason=FabricValue["IncompleteReason"],
    )
    Domains = tuple(
        ComponentTerminalAccessDomain(
            Signal=Value["Signal"],
            Terminal=Position(Value["Terminal"]),
            TerminalRole=Value["TerminalRole"],
            TerminalFingerprint=Value["TerminalFingerprint"],
            Candidates=tuple(
                ComponentTerminalAccessCandidate(
                    CandidateFingerprint=(
                        Candidate["CandidateFingerprint"]
                    ),
                    Attachment=Position(Candidate["Attachment"]),
                    Path=tuple(map(Position, Candidate["Path"])),
                    Claims=Claims(Candidate["Claims"]),
                    Layer=int(Candidate["Layer"]),
                    Cost=int(Candidate["Cost"]),
                )
                for Candidate in Value["Candidates"]
            ),
            Complete=bool(Value["Complete"]),
        )
        for Value in Data["OwnedTerminalDomains"]
    )
    InterfaceValue = Data["Interface"]
    PhysicalPorts = tuple(
        PhysicalComponentPortReservation(
            Signal=Value["Signal"],
            Direction=Value["Direction"],
            OwnedTerminals=tuple(map(
                Position,
                Value["OwnedTerminals"],
            )),
            OwnedTerminalFingerprints=tuple(
                Value["OwnedTerminalFingerprints"]
            ),
            OwnedCandidateFingerprints=tuple(
                Value["OwnedCandidateFingerprints"]
            ),
            FabricDomainFingerprint=(
                Value["FabricDomainFingerprint"]
            ),
            FabricAttachment=Position(Value["FabricAttachment"]),
            Attachment=Position(Value["Attachment"]),
            LocalPath=tuple(map(Position, Value["LocalPath"])),
            GlobalPath=tuple(map(Position, Value["GlobalPath"])),
            Claims=Claims(Value["Claims"]),
            Capacity=int(Value["Capacity"]),
            ReservationFingerprint=Value["ReservationFingerprint"],
        )
        for Value in InterfaceValue["PhysicalPortReservations"]
    )
    Interface = ClosedComponentInterface(
        InterfaceFingerprint=InterfaceValue["InterfaceFingerprint"],
        ComponentId=InterfaceValue["ComponentId"],
        OwnedSignals=tuple(InterfaceValue["OwnedSignals"]),
        Ports=tuple(
            ComponentInterfacePort(
                Signal=Value["Signal"],
                Direction=Value["Direction"],
                OwnedTerminals=tuple(map(
                    Position,
                    Value["OwnedTerminals"],
                )),
                ExternalTerminalCount=int(
                    Value["ExternalTerminalCount"]
                ),
                Capacity=int(Value["Capacity"]),
            )
            for Value in InterfaceValue["Ports"]
        ),
        PhysicalPortReservations=PhysicalPorts,
        PhysicalAssemblyPlanFingerprint="cla4-tree-dp-fixture-plan",
        Complete=bool(InterfaceValue["Complete"]),
    )
    ResourceValue = Data["ResourceGraph"]
    ResourceGraph = RoutingResourceGraph(
        ActualBlocks=frozenset(map(
            Position,
            ResourceValue["ActualBlocks"],
        )),
        ElectricalBlocks=frozenset(map(
            Position,
            ResourceValue["ElectricalBlocks"],
        )),
        SolidBlocks=frozenset(map(
            Position,
            ResourceValue["SolidBlocks"],
        )),
        GraphVersion=ResourceValue["GraphVersion"],
    )
    Plan = PhysicalComponentAssemblyPlan(
        PlanFingerprint="cla4-tree-dp-fixture-plan",
        PortAssignmentFingerprint="cla4-tree-dp-fixture-ports",
        PlacementFingerprint=Data["PlacementFingerprint"],
        ComponentGraphFingerprint="cla4-tree-dp-fixture-component",
        ResourceGraphFingerprint="cla4-tree-dp-fixture-resource",
        TechnologyFingerprint="cla4-tree-dp-fixture-technology",
        InterfaceFingerprint=Interface.InterfaceFingerprint,
        ComponentId=Interface.ComponentId,
        EnvelopeMinimum=min(Fabric.Nodes),
        EnvelopeMaximum=max(Fabric.Nodes),
        KeepoutClaims=RoutingResourceClaims(),
        Ports=PhysicalPorts,
        Channels=(),
    )
    Problem = ComponentRoutingProblem(
        ProblemFingerprint=Data["ProblemFingerprint"],
        PlacementFingerprint=Data["PlacementFingerprint"],
        LocalTemplateFingerprint=Data["LocalTemplateFingerprint"],
        SelectedClusters=tuple(map(int, Data["SelectedClusters"])),
        ComponentSignals=tuple(Data["ComponentSignals"]),
        LocalClaims=tuple(map(LocalClaim, Data["LocalClaims"])),
        Fabric=Fabric,
        OwnedTerminalDomains=Domains,
        ExternalContinuationTerminals=tuple(
            (Signal, Position(Terminal), Role)
            for Signal, Terminal, Role
            in Data["ExternalContinuationTerminals"]
        ),
        ForeignEscapeDomains=(),
        MaximumPowerDistance=int(Data["MaximumPowerDistance"]),
        DomainComplete=bool(Data["DomainComplete"]),
        ResourceGraph=ResourceGraph,
        MaximumWork=int(Data["MaximumWork"]),
        ImmutableClaims=tuple(map(
            LocalClaim,
            Data["ImmutableClaims"],
        )),
        Interface=Interface,
        PhysicalAssemblyPlan=Plan,
        ReservedGlobalClaimsBySignal=tuple(
            (Signal, Claims(Value))
            for Signal, Value
            in Data["ReservedGlobalClaimsBySignal"]
        ),
    )
    return Data, Problem


def test_captured_cla4_tree_frontier_fixture_completes_under_gate():
    Data, Problem = _LoadCla4TreeDpFixture()
    SymbolicNetStateCache = {}
    Started = monotonic()
    First = SolveComponentRoutingProblemDynamic(
        Problem,
        DeadlineSeconds=30.0,
    )
    RuntimeSeconds = monotonic() - Started
    Second = SolveComponentRoutingProblemDynamic(
        Problem,
        DeadlineSeconds=30.0,
    )
    Dispatched = SolveComponentRoutingProblem(
        Problem,
        DeadlineSeconds=30.0,
    )
    CapacityProof = SolveComponentRoutingProblem(
        Problem,
        DeadlineSeconds=30.0,
        StopAfterSymbolicCapacityProof=True,
    )
    CachedCapacityProof = SolveComponentRoutingProblemDynamic(
        Problem,
        DeadlineSeconds=30.0,
        StopAfterSymbolicCapacityProof=True,
        SymbolicNetStateCache=SymbolicNetStateCache,
    )
    ReusedCapacityProof = SolveComponentRoutingProblemDynamic(
        Problem,
        DeadlineSeconds=30.0,
        StopAfterSymbolicCapacityProof=True,
        SymbolicNetStateCache=SymbolicNetStateCache,
    )

    assert First.Status == Data["ExpectedStatus"]
    assert Second.Status == First.Status
    assert Dispatched.Status == First.Status
    assert Second.ProofFingerprint == First.ProofFingerprint
    assert Dispatched.ProofFingerprint == First.ProofFingerprint
    assert CapacityProof.Status == "architectural-unsatisfiable"
    assert CapacityProof.Template is None
    assert CapacityProof.Diagnostics["SymbolicCapacityProofComplete"] is True
    assert CapacityProof.Diagnostics["LocalUnsatCoreComplete"] is True
    assert CapacityProof.Diagnostics["LocalUnsatCoreKind"] in {
        "complete-symbolic-capacity-pair",
        "tree-frontier-empty-signal",
    }
    assert CapacityProof.Diagnostics["LocalUnsatCoreSignals"]
    assert CachedCapacityProof.ProofFingerprint == (
        ReusedCapacityProof.ProofFingerprint
    )
    StoredNetStateCount = CachedCapacityProof.Diagnostics[
        "SymbolicNetStateCacheStoreCount"
    ]
    assert 0 < StoredNetStateCount <= len(Problem.ComponentSignals)
    assert ReusedCapacityProof.Diagnostics[
        "SymbolicNetStateCacheHitCount"
    ] == StoredNetStateCount
    assert ReusedCapacityProof.Diagnostics[
        "SymbolicNetStateCacheStoreCount"
    ] == 0
    assert RuntimeSeconds < 30.0
    assert First.Diagnostics["SolverKind"] == "tree-frontier-dp-v1"
    assert Dispatched.Diagnostics["SolverKind"] == "tree-frontier-dp-v1"
    assert First.Diagnostics["CompleteTreesMaterialized"] == 0
    assert First.Diagnostics["ComponentSignalCount"] == 7
    assert First.Diagnostics["OwnedTerminalDomainCount"] == 9


def test_physically_identical_access_derivations_share_one_net_variant():
    Base = _Problem()
    Source = Base.OwnedTerminalDomains[0]
    Candidate = Source.Candidates[0]
    Duplicate = ComponentTerminalAccessCandidate(
        CandidateFingerprint="different-enumeration-identity",
        Attachment=Candidate.Attachment,
        Path=Candidate.Path,
        Claims=Candidate.Claims,
        Layer=Candidate.Layer,
        Cost=Candidate.Cost + 1,
    )
    Problem = ComponentRoutingProblem(
        **{
            **Base.__dict__,
            "OwnedTerminalDomains": (
                ComponentTerminalAccessDomain(
                    **{
                        **Source.__dict__,
                        "Candidates": (Candidate, Duplicate),
                    },
                ),
                Base.OwnedTerminalDomains[1],
            ),
        },
    )
    Result = SolveComponentRoutingProblem(Problem)
    assert Result.Feasible
    Diagnostics = (
        Result.Diagnostics["VariantDiagnosticsBySignal"]["Alpha"]
    )
    assert Diagnostics["AccessCombinationCount"] == 2
    assert Diagnostics["CanonicalAccessStateCount"] == 1
    assert Diagnostics["DuplicateCanonicalAccessStateCount"] == 1
    assert Diagnostics["NetVariantBuildCount"] == 1
    assert Diagnostics["RoutedVariantCount"] == 1


def test_canonical_access_state_preserves_power_and_egress_identity():
    Problem = _Problem()
    Domains = Problem.OwnedTerminalDomains
    Candidates = tuple(Domain.Candidates[0] for Domain in Domains)
    FabricSubtree = (
        frozenset(Problem.Fabric.Nodes),
        frozenset(Problem.Fabric.Edges),
    )
    Base = _BuildCanonicalAccessCombinationKey(
        Problem,
        "Alpha",
        Domains,
        Candidates,
        (),
        0,
        FabricSubtree,
    )
    DuplicateCandidates = (
        replace(
            Candidates[0],
            CandidateFingerprint="alternate-logical-identity",
            Cost=Candidates[0].Cost + 10,
        ),
        Candidates[1],
    )
    Duplicate = _BuildCanonicalAccessCombinationKey(
        Problem,
        "Alpha",
        Domains,
        DuplicateCandidates,
        (),
        0,
        FabricSubtree,
    )
    DifferentPowerDistance = _BuildCanonicalAccessCombinationKey(
        replace(
            Problem,
            MaximumPowerDistance=Problem.MaximumPowerDistance - 1,
        ),
        "Alpha",
        Domains,
        Candidates,
        (),
        0,
        FabricSubtree,
    )
    DifferentEgress = _BuildCanonicalAccessCombinationKey(
        Problem,
        "Alpha",
        Domains,
        Candidates,
        ((2, 7, 0), (2, 8, 0)),
        0,
        FabricSubtree,
    )

    assert Duplicate == Base
    assert DifferentPowerDistance != Base
    assert DifferentEgress != Base


def test_no_powered_variant_reports_complete_local_unsat_core():
    Problem = replace(
        _Problem(),
        ImmutableClaims=(SimpleNamespace(
            Signal="OutsideLocal",
            Claims=_Claims((0, 7, 0)),
        ),),
    )

    Result = SolveComponentRoutingProblem(
        Problem,
        DiscoveryVariantLimit=None,
    )

    assert Result.Status == "architectural-unsatisfiable"
    assert Result.Diagnostics["LocalUnsatSignal"] == "Alpha"
    assert Result.Diagnostics["LocalUnsatCoreSignals"] == [
        "Alpha",
        "OutsideLocal",
    ]
    assert Result.Diagnostics["LocalUnsatCoreComplete"]
    assert Result.Diagnostics["LocalUnsatCoreFingerprint"]


def test_tree_power_dp_places_repeaters_on_long_trunk():
    Nodes = tuple((Index, 7, 0) for Index in range(10))
    Fabric = BuildComponentRoutingFabric(_Channel(Nodes))
    Problem = _Problem(
        Fabric=Fabric,
        MaximumPowerDistance=3,
    )
    Problem = ComponentRoutingProblem(
        **{
            **Problem.__dict__,
            "OwnedTerminalDomains": (
                _Domain(
                    "Alpha",
                    Nodes[0],
                    "source",
                    _Candidate((Nodes[0],)),
                ),
                _Domain(
                    "Alpha",
                    Nodes[-1],
                    "target",
                    _Candidate((Nodes[-1],)),
                ),
            ),
        }
    )
    Result = SolveComponentRoutingProblem(Problem)
    assert Result.Feasible
    assert Result.Template is not None
    assert len(Result.Template.Nets[0].Repeaters) >= 2


def test_foreign_escape_must_respect_immutable_outside_local_claims():
    ForeignCandidate = _Candidate(((10, 3, 10), (11, 3, 10)))
    Problem = _Problem(
        Foreign=(
            _Domain(
                "Foreign",
                (10, 3, 10),
                "foreign-target",
                ForeignCandidate,
            ),
        ),
    )
    Problem = ComponentRoutingProblem(
        **{
            **Problem.__dict__,
            "ImmutableClaims": (
                SimpleNamespace(
                    Signal="OutsideLocal",
                    Claims=_Claims((11, 3, 10)),
                ),
            ),
        }
    )
    Result = SolveComponentRoutingProblem(Problem)
    assert Result.Status == "architectural-unsatisfiable"
    assert Result.Template is None


def test_same_signal_claim_fragments_still_obey_physical_self_conflicts():
    Existing = _Claims((10, 2, 10))
    Colliding = _Claims((10, 3, 10))
    assert ComponentClaimsCompatibleForOwners(
        "Shared",
        Existing,
        "Shared",
        Existing,
    )
    assert not ComponentClaimsCompatibleForOwners(
        "Shared",
        Existing,
        "Shared",
        Colliding,
    )


def test_frozen_foreign_source_exports_boundary_as_global_root():
    Claim = SimpleNamespace(
        Signal="ForeignSource",
        ClusterId=-2,
        Root=(4, 1, 4),
        BoundaryNodes=((8, 4, 8),),
    )
    Profile = NetRoutingProfile(
        Signal="ForeignSource",
        Root=(4, 1, 4),
        Targets=((20, 1, 20),),
        Span=32,
        Fanout=1,
        RetryCount=0,
        Criticality=0,
        IsTrunk=False,
        SourceAccessPath=((4, 1, 4),),
        TargetAccessPaths={(20, 1, 20): ((20, 1, 20),)},
    )
    Result = ApplyRoutedComponentGlobalProfiles(
        SimpleNamespace(
            RoutedComponentTemplates=(),
            LocalRouteClaims=(Claim,),
        ),
        {"ForeignSource": Profile},
    )
    assert Result["ForeignSource"].Root == (8, 4, 8)
    assert Result["ForeignSource"].SourceAccessPath == ((8, 4, 8),)


def test_frozen_foreign_source_replaces_original_portal_with_export():
    Candidate = _Candidate(((4, 1, 4), (5, 2, 5)))
    Template = SimpleNamespace(
        ForeignEscapeReservations=(
            ("ForeignSource", (4, 1, 4), Candidate),
        ),
    )
    Placed = SimpleNamespace(
        RoutedComponentTemplates=(Template,),
        PlacedGates=(
            SimpleNamespace(OutputPin=(4, 1, 4)),
        ),
        LocalRouteClaims=(
            SimpleNamespace(
                Signal="ForeignSource",
                Root=(4, 1, 4),
                ClusterId=-2,
            ),
        ),
    )
    Portals, Diagnostics = PreserveRoutedComponentForeignEscapes(
        Placed,
        {
            ("ForeignSource", (4, 1, 4), 0): (
                SimpleNamespace(),
            ),
        },
    )
    assert ("ForeignSource", (4, 1, 4), 0) not in Portals
    ExportKey = ("ForeignSource", (5, 2, 5), 0)
    assert ExportKey in Portals
    assert Portals[ExportKey][0].Path == ((5, 2, 5),)
    assert Diagnostics["ExportedSourcePortCount"] == 1


def test_frozen_foreign_target_replaces_pin_with_boundary_endpoint():
    Claim = SimpleNamespace(
        Signal="ForeignTarget",
        ClusterId=-2,
        Root=(20, 1, 20),
        BoundaryNodes=((18, 4, 20),),
    )
    Profile = NetRoutingProfile(
        Signal="ForeignTarget",
        Root=(4, 1, 4),
        Targets=((20, 1, 20), (24, 1, 24)),
        Span=40,
        Fanout=2,
        RetryCount=0,
        Criticality=0,
        IsTrunk=False,
        SourceAccessPath=((4, 1, 4),),
        TargetAccessPaths={
            (20, 1, 20): ((20, 1, 20),),
            (24, 1, 24): ((24, 1, 24),),
        },
    )

    Result = ApplyRoutedComponentGlobalProfiles(
        SimpleNamespace(
            RoutedComponentTemplates=(),
            LocalRouteClaims=(Claim,),
        ),
        {"ForeignTarget": Profile},
    )

    assert Result["ForeignTarget"].Targets == (
        (18, 4, 20),
        (24, 1, 24),
    )
    assert Result["ForeignTarget"].TargetAccessPaths[
        (18, 4, 20)
    ] == ((18, 4, 20),)
    assert (20, 1, 20) not in (
        Result["ForeignTarget"].TargetAccessPaths
    )


def test_external_port_and_foreign_escape_survive_materialization():
    ForeignCandidate = _Candidate(((10, 3, 10), (11, 3, 10)))
    Foreign = _Domain(
        "Foreign",
        (10, 3, 10),
        "foreign-target",
        ForeignCandidate,
    )
    Problem = _Problem(
        Foreign=(Foreign,),
        External=(("Alpha", (9, 3, 9), "target"),),
    )
    Result = SolveComponentRoutingProblem(Problem)
    assert Result.Feasible
    assert Result.Template is not None
    assert Result.Template.ExportedPorts
    assert Result.Template.ForeignEscapeReservations[0][2] == ForeignCandidate

    Channel = SimpleNamespace(
        PhysicalModel="test-tree-fabric",
        ChannelFingerprint="channel",
        Lanes=(
            SimpleNamespace(
                Cells=((0, 7, 0), (1, 7, 0), (2, 7, 0)),
                IngressNodes=((0, 7, 0), (2, 7, 0)),
            ),
        ),
    )
    Placed = PlacedDesign(
        Module=SimpleNamespace(),
        PlacedGates=[],
        LocalRouteClaims=(),
        LocalRouteDiagnostics={},
        RoutedComponentTemplates=(),
        ClusterBoundaryLeaseRequests=(SimpleNamespace(Signal="Foreign"),),
        InterClusterRoutingChannel=Channel,
    )
    Materialized = MaterializeRoutedComponentTemplate(
        Placed,
        Result.Template,
    )
    assert Materialized.RoutedComponentTemplates == (Result.Template,)
    assert Materialized.ClusterBoundaryLeaseRequests == ()
    ForeignClaims = tuple(
        Claim
        for Claim in Materialized.LocalRouteClaims
        if Claim.ClusterId == -2
    )
    assert len(ForeignClaims) == 1
    assert ForeignClaims[0].Signal == "Foreign"
    assert ForeignClaims[0].Root == Foreign.Terminal
    assert ForeignClaims[0].ConnectedTargets == (Foreign.Terminal,)
    assert ForeignClaims[0].BoundaryNodes == (
        ForeignCandidate.Path[-1],
    )
    assert ForeignClaims[0].Nodes == frozenset(ForeignCandidate.Path)
    assert (
        Materialized.LocalRouteDiagnostics[
            "__RoutedComponentGlobalHandoff__"
        ]["FrozenForeignEscapeClaimCount"]
        == 1
    )
    assert (
        Materialized.LocalRouteDiagnostics[
            "__RoutedComponentGlobalHandoff__"
        ]["RetiredClusterBoundaryLeaseRequestCount"]
        == 1
    )
    assert Materialized.LocalRouteClaims[0].BoundaryNodes
    assert (
        Materialized.LocalRouteDiagnostics[
            "__RoutedComponentTemplate__"
        ]["ExportedPortFingerprint"]
        == Result.Template.ExportedPortFingerprint
    )
    assert ValidateRoutedComponentHandoff(
        Materialized,
        Result.Template,
        PlacementFingerprint="placement",
        LocalTemplateFingerprint="local",
    )["Valid"]
    AugmentedTemplate = replace(
        Result.Template,
        FabricFingerprint="augmented-exact-access-fabric",
    )
    AugmentedMaterialized = MaterializeRoutedComponentTemplate(
        Placed,
        AugmentedTemplate,
    )
    AugmentedHandoff = ValidateRoutedComponentHandoff(
        AugmentedMaterialized,
        AugmentedTemplate,
        PlacementFingerprint="placement",
        LocalTemplateFingerprint="local",
    )
    assert AugmentedHandoff["Valid"]
    assert AugmentedHandoff["FabricAugmentedForExactAccess"]
    assert (
        AugmentedHandoff["ArchivedFabricFingerprint"]
        != AugmentedHandoff["FabricFingerprint"]
    )
    Portal = SimpleNamespace(
        Path=ForeignCandidate.Path,
        Claims=ForeignCandidate.Claims,
        Cost=0,
        PortalId="foreign",
    )
    AlternativePortal = SimpleNamespace(
        Path=((10, 5, 10), (11, 5, 10)),
        Claims=_Claims((10, 5, 10), (11, 5, 10)),
        Cost=1,
        PortalId="foreign-alternative",
    )
    Portals, Diagnostics = PreserveRoutedComponentForeignEscapes(
        Materialized,
        {
            ("Foreign", (10, 3, 10), 1): (Portal,),
            ("Foreign", (10, 3, 10), 2): (AlternativePortal,),
        },
    )
    assert ("Foreign", (10, 3, 10), 1) not in Portals
    assert ("Foreign", (10, 3, 10), 2) not in Portals
    assert Diagnostics["ConsumedByFrozenClaimCount"] == 1
    assert Diagnostics["Complete"]

    Entering = SolveComponentRoutingProblem(_Problem(
        External=(("Alpha", (-5, 7, 0), "source"),),
    ))
    assert Entering.Feasible
    assert Entering.Template is not None
    EnteringNet = Entering.Template.Nets[0]
    assert EnteringNet.Root in EnteringNet.ExportedPorts


def test_external_continuation_access_is_solved_and_frozen():
    SelectedCandidate = _Candidate(((20, 3, 20), (21, 3, 20)))
    Continuation = _Domain(
        "Alpha",
        (20, 3, 20),
        "continuation-target",
        SelectedCandidate,
    )
    Problem = replace(
        _Problem(
            External=(("Alpha", Continuation.Terminal, "target"),),
        ),
        ExternalContinuationDomains=(Continuation,),
    )

    Result = SolveComponentRoutingProblem(Problem)

    assert Result.Feasible
    assert Result.Template is not None
    assert Result.Template.ExternalContinuationReservations == (
        ("Alpha", Continuation.Terminal, SelectedCandidate),
    )
    Placed = PlacedDesign(
        Module=SimpleNamespace(),
        PlacedGates=[],
        LocalRouteClaims=(),
        LocalRouteDiagnostics={},
        RoutedComponentTemplates=(),
    )
    Materialized = MaterializeRoutedComponentTemplate(
        Placed,
        Result.Template,
    )
    ContinuationClaims = tuple(
        Claim
        for Claim in Materialized.LocalRouteClaims
        if Claim.ClusterId == -3
    )
    assert len(ContinuationClaims) == 1
    assert ContinuationClaims[0].Signal == "Alpha"
    assert ContinuationClaims[0].Root == Continuation.Terminal
    assert ContinuationClaims[0].Nodes == frozenset(
        SelectedCandidate.Path
    )


def test_external_continuation_handoff_retains_only_selected_portal():
    SelectedPortal = _Portal(
        "Alpha",
        (20, 3, 20),
        ((20, 3, 20), (21, 3, 20)),
        "selected",
    )
    OtherPortal = _Portal(
        "Alpha",
        (20, 3, 20),
        ((20, 3, 20), (20, 3, 21)),
        "other",
    )
    Candidate = _Candidate(SelectedPortal.Path)
    Template = SimpleNamespace(
        ExternalContinuationReservations=(
            ("Alpha", (20, 3, 20), Candidate),
        ),
        ForeignEscapeReservations=(),
    )

    Portals, Diagnostics = PreserveRoutedComponentForeignEscapes(
        SimpleNamespace(
            RoutedComponentTemplates=(Template,),
            LocalRouteClaims=(),
            PlacedGates=(),
        ),
        {
            ("Alpha", (20, 3, 20), 0): (
                OtherPortal,
                SelectedPortal,
            ),
        },
    )

    assert Portals[("Alpha", (20, 3, 20), 0)] == (
        SelectedPortal,
    )
    assert Diagnostics["ContinuationRequiredCount"] == 1
    assert Diagnostics["ContinuationPreservedCount"] == 1
    assert Diagnostics["ContinuationMissingCount"] == 0
    assert Diagnostics["Complete"]


def test_cut_derived_export_port_nogood_selects_distinct_variant():
    Problem = _Problem(
        External=(("Alpha", (9, 3, 9), "target"),),
    )
    PortfolioCache = {}
    First = SolveComponentRoutingProblem(
        Problem,
        VariantPortfolioCache=PortfolioCache,
    )
    assert First.Feasible and First.Template is not None
    FirstNet = First.Template.Nets[0]
    Second = SolveComponentRoutingProblem(
        Problem,
        ForbiddenExportPortsBySignal={
            "Alpha": FirstNet.ExportedPorts,
        },
        VariantPortfolioCache=PortfolioCache,
    )
    assert Second.Feasible and Second.Template is not None
    assert (
        Second.Template.Nets[0].ExportedPorts
        != FirstNet.ExportedPorts
    )
    assert (
        Second.Diagnostics["VariantDiagnosticsBySignal"]["Alpha"][
            "PortfolioCacheHit"
        ]
    )


def test_complete_net_portfolio_cache_reuses_rigid_translation_and_rename():
    PortfolioCache = {}
    First = SolveComponentRoutingProblem(
        _Problem("Alpha"),
        VariantPortfolioCache=PortfolioCache,
        DiscoveryVariantLimit=None,
    )
    assert First.Feasible and First.Template is not None

    Delta = (20, 0, 11)

    def Move(Position):
        return tuple(
            Position[Index] + Delta[Index]
            for Index in range(3)
        )

    def MoveClaims(Claims):
        return RoutingResourceClaims(
            WireCells=frozenset(map(Move, Claims.WireCells)),
            SupportCells=frozenset(map(Move, Claims.SupportCells)),
            RequiredAirCells=frozenset(
                map(Move, Claims.RequiredAirCells)
            ),
            ElectricalCells=frozenset(
                map(Move, Claims.ElectricalCells)
            ),
        )

    def MoveCandidate(Candidate):
        return replace(
            Candidate,
            CandidateFingerprint=(
                f"renamed-{Candidate.CandidateFingerprint}"
            ),
            Attachment=Move(Candidate.Attachment),
            Path=tuple(map(Move, Candidate.Path)),
            Claims=MoveClaims(Candidate.Claims),
        )

    Base = _Problem("Alpha")
    Translated = replace(
        Base,
        ProblemFingerprint="translated-and-renamed-problem",
        PlacementFingerprint="translated-placement",
        ComponentSignals=("Omega",),
        Fabric=replace(
            Base.Fabric,
            FabricFingerprint="translated-fabric",
            Nodes=tuple(map(Move, Base.Fabric.Nodes)),
            Edges=tuple(
                (Move(FirstNode), Move(SecondNode))
                for FirstNode, SecondNode in Base.Fabric.Edges
            ),
            IngressNodes=tuple(map(Move, Base.Fabric.IngressNodes)),
        ),
        OwnedTerminalDomains=tuple(
            replace(
                Domain,
                Signal="Omega",
                Terminal=Move(Domain.Terminal),
                TerminalFingerprint=(
                    f"renamed-{Domain.TerminalRole}"
                ),
                Candidates=tuple(
                    MoveCandidate(Candidate)
                    for Candidate in Domain.Candidates
                ),
            )
            for Domain in Base.OwnedTerminalDomains
        ),
    )
    Second = SolveComponentRoutingProblem(
        Translated,
        VariantPortfolioCache=PortfolioCache,
        DiscoveryVariantLimit=None,
    )

    assert Second.Feasible and Second.Template is not None
    Diagnostics = Second.Diagnostics[
        "VariantDiagnosticsBySignal"
    ]["Omega"]
    assert Diagnostics["PortfolioCacheHit"]
    assert Diagnostics["PortfolioTranslationValidated"]
    assert Diagnostics["PortfolioTranslationDelta"] == list(Delta)
    assert Second.Template.Nets[0].Signal == "Omega"
    assert Second.Template.Nets[0].Nodes == frozenset(
        Move(Position)
        for Position in First.Template.Nets[0].Nodes
    )


def test_complete_net_portfolio_cache_does_not_cross_resource_identity():
    PortfolioCache = {}
    EmptyGraph = RoutingResourceGraph(
        ActualBlocks=frozenset(),
        ElectricalBlocks=frozenset(),
        SolidBlocks=frozenset(),
    )
    First = SolveComponentRoutingProblem(
        replace(_Problem(), ResourceGraph=EmptyGraph),
        VariantPortfolioCache=PortfolioCache,
        DiscoveryVariantLimit=None,
    )
    assert First.Feasible

    ChangedGraph = RoutingResourceGraph(
        ActualBlocks=frozenset(((100, 0, 100),)),
        ElectricalBlocks=frozenset(),
        SolidBlocks=frozenset(),
    )
    Second = SolveComponentRoutingProblem(
        replace(
            _Problem(),
            ProblemFingerprint="changed-resource-identity",
            ResourceGraph=ChangedGraph,
        ),
        VariantPortfolioCache=PortfolioCache,
        DiscoveryVariantLimit=None,
    )

    assert Second.Feasible
    assert not Second.Diagnostics["VariantDiagnosticsBySignal"]["Alpha"][
        "PortfolioCacheHit"
    ]


def test_reserved_global_claim_revalidates_cached_complete_tree():
    PortfolioCache = {}
    NetCache = {}
    First = SolveComponentRoutingProblem(
        _Problem(),
        VariantPortfolioCache=PortfolioCache,
        NetVariantConstructionCache=NetCache,
        DiscoveryVariantLimit=None,
    )
    assert First.Feasible

    Blocked = replace(
        _Problem(),
        ProblemFingerprint="reserved-global-blocked-problem",
        ReservedGlobalClaimsBySignal=((
            "ReservedPeer",
            _Claims((1, 7, 0)),
        ),),
    )
    Second = SolveComponentRoutingProblem(
        Blocked,
        VariantPortfolioCache=PortfolioCache,
        NetVariantConstructionCache=NetCache,
        DiscoveryVariantLimit=None,
    )

    assert not Second.Feasible
    assert "ReservedPeer" in Second.Diagnostics[
        "VariantDiagnosticsBySignal"
    ]["Alpha"]["ImmutableConflictSignals"]


def test_reserved_access_conflict_cache_is_scoped_to_claim_context():
    AccessCache = {}
    Problem = _Problem()
    Candidates = tuple(
        Domain.Candidates[0]
        for Domain in Problem.OwnedTerminalDomains
    )
    FirstRejections = {}
    First = _BuildNetVariant(
        Problem,
        "Alpha",
        Problem.OwnedTerminalDomains,
        Candidates,
        RejectionCounts=FirstRejections,
        ImmutableAccessConflictCache=AccessCache,
    )
    assert First is not None

    Blocked = replace(
        _Problem(),
        ReservedGlobalClaimsBySignal=((
            "ReservedPeer",
            _Claims((0, 7, 0)),
        ),),
    )
    SecondRejections = {}
    Second = _BuildNetVariant(
        Blocked,
        "Alpha",
        Blocked.OwnedTerminalDomains,
        Candidates,
        RejectionCounts=SecondRejections,
        ImmutableAccessConflictCache=AccessCache,
    )

    assert Second is None
    assert SecondRejections[
        "immutable-local-access-conflict"
    ] > 0


def test_reserved_blocker_provenance_separates_port_from_global_route():
    def SolveWithPortClaims(PortClaims):
        ReservedPort = PhysicalComponentPortReservation(
            Signal="ReservedPeer",
            Direction="output",
            OwnedTerminals=((8, 7, 0),),
            OwnedTerminalFingerprints=("reserved-terminal",),
            OwnedCandidateFingerprints=(),
            FabricDomainFingerprint="reserved-domain",
            FabricAttachment=(8, 7, 0),
            Attachment=(9, 7, 0),
            LocalPath=((8, 7, 0), (9, 7, 0)),
            GlobalPath=((9, 7, 0), (10, 7, 0)),
            Claims=PortClaims,
            Capacity=1,
            ReservationFingerprint="reserved-port",
        )
        Interface = ClosedComponentInterface(
            InterfaceFingerprint="reserved-interface",
            ComponentId=1,
            OwnedSignals=("Alpha", "ReservedPeer"),
            Ports=(),
            PhysicalPortReservations=(ReservedPort,),
        )
        return SolveComponentRoutingProblem(
            replace(
                _Problem(),
                Interface=Interface,
                ReservedGlobalClaimsBySignal=((
                    "ReservedPeer",
                    _Claims((0, 7, 0)),
                ),),
            ),
            DiscoveryVariantLimit=None,
        )

    PortBlocked = SolveWithPortClaims(_Claims((0, 7, 0)))
    RouteBlocked = SolveWithPortClaims(_Claims((9, 7, 0)))
    PortDiagnostics = PortBlocked.Diagnostics[
        "VariantDiagnosticsBySignal"
    ]["Alpha"]
    RouteDiagnostics = RouteBlocked.Diagnostics[
        "VariantDiagnosticsBySignal"
    ]["Alpha"]

    assert PortDiagnostics[
        "ReservedPortContractConflictSignals"
    ] == ["ReservedPeer"]
    assert not PortDiagnostics[
        "ReservedGlobalRouteConflictSignals"
    ]
    assert RouteDiagnostics[
        "ReservedGlobalRouteConflictSignals"
    ] == ["ReservedPeer"]
    assert not RouteDiagnostics[
        "ReservedPortContractConflictSignals"
    ]


def test_completed_component_template_cache_reuses_translation():
    Interface = ClosedComponentInterface(
        InterfaceFingerprint="completed-cache-interface",
        ComponentId=7,
        OwnedSignals=("Alpha",),
        Ports=(),
    )
    FirstProblem = replace(_Problem(), Interface=Interface)
    First = CompileClosedComponent(
        FirstProblem,
        DiscoveryVariantLimit=None,
    )
    assert First.Feasible and First.Template is not None
    assert not First.Diagnostics["CompletedTemplateCacheHit"]

    Delta = (31, 0, 13)

    def Move(Position):
        return tuple(
            Position[Index] + Delta[Index]
            for Index in range(3)
        )

    def MoveClaims(Claims):
        return RoutingResourceClaims(
            WireCells=frozenset(map(Move, Claims.WireCells)),
            SupportCells=frozenset(map(Move, Claims.SupportCells)),
            RequiredAirCells=frozenset(
                map(Move, Claims.RequiredAirCells)
            ),
            ElectricalCells=frozenset(
                map(Move, Claims.ElectricalCells)
            ),
        )

    Translated = replace(
        FirstProblem,
        ProblemFingerprint="completed-cache-translated",
        PlacementFingerprint="translated-placement",
        Fabric=BuildComponentRoutingFabric(_Channel(tuple(
            Move(Position)
            for Position in ((0, 7, 0), (1, 7, 0), (2, 7, 0))
        ))),
        OwnedTerminalDomains=tuple(
            replace(
                Domain,
                Terminal=Move(Domain.Terminal),
                Candidates=tuple(
                    replace(
                        Candidate,
                        Attachment=Move(Candidate.Attachment),
                        Path=tuple(map(Move, Candidate.Path)),
                        Claims=MoveClaims(Candidate.Claims),
                    )
                    for Candidate in Domain.Candidates
                ),
            )
            for Domain in FirstProblem.OwnedTerminalDomains
        ),
    )
    Second = CompileClosedComponent(
        Translated,
        DiscoveryVariantLimit=None,
    )

    assert Second.Feasible and Second.Template is not None
    assert Second.Diagnostics["CompletedTemplateCacheHit"]
    assert (
        Second.Diagnostics["CompletedTemplateTranslationDelta"]
        == list(Delta)
    )
    assert Second.Template.Nets[0].Nodes == frozenset(
        Move(Position)
        for Position in First.Template.Nets[0].Nodes
    )


def test_complete_net_portfolio_cache_rejects_changed_fabric_topology():
    PortfolioCache = {}
    SolveComponentRoutingProblem(
        _Problem(),
        VariantPortfolioCache=PortfolioCache,
        DiscoveryVariantLimit=None,
    )
    Changed = replace(_Problem(
        Fabric=BuildComponentRoutingFabric(_Channel(
            ((0, 7, 0), (1, 7, 0), (2, 7, 0)),
            ((1, 7, 0), (1, 7, 1)),
        )),
    ), ProblemFingerprint="changed-topology-problem")
    Result = SolveComponentRoutingProblem(
        Changed,
        VariantPortfolioCache=PortfolioCache,
        DiscoveryVariantLimit=None,
    )

    assert Result.Feasible
    assert not Result.Diagnostics[
        "VariantDiagnosticsBySignal"
    ]["Alpha"]["PortfolioCacheHit"]


def test_progressive_discovery_reuses_net_construction_work():
    NetCache = {}
    ClaimsCache = {}
    DiscoveryCache = {}
    First = SolveComponentRoutingProblem(
        _Problem(),
        DiscoveryVariantLimit=1,
        NetVariantConstructionCache=NetCache,
        RouteClaimsConstructionCache=ClaimsCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )
    assert First.Feasible
    assert NetCache
    assert ClaimsCache
    assert DiscoveryCache

    Second = SolveComponentRoutingProblem(
        _Problem(),
        DiscoveryVariantLimit=2,
        NetVariantConstructionCache=NetCache,
        RouteClaimsConstructionCache=ClaimsCache,
        NetVariantDiscoveryStateCache=DiscoveryCache,
    )

    assert Second.Feasible
    assert (
        Second.Diagnostics[
            "NetVariantConstructionCacheInitialCount"
        ] > 0
    )
    assert (
        Second.Diagnostics[
            "RouteClaimsConstructionCacheInitialCount"
        ] > 0
    )
    assert (
        Second.Diagnostics[
            "NetVariantDiscoveryStateCacheInitialCount"
        ] > 0
    )


def test_signal_scoped_discovery_limit_overrides_global_limit():
    Result = SolveComponentRoutingProblem(
        _Problem(),
        DiscoveryVariantLimit=1,
        DiscoveryVariantLimitsBySignal={"Alpha": None},
    )

    assert Result.Feasible
    Alpha = Result.Diagnostics[
        "VariantDiagnosticsBySignal"
    ]["Alpha"]
    assert Alpha["DiscoveryVariantLimit"] is None
    assert Alpha["DiscoveryPortfolioComplete"]


def test_cut_derived_foreign_witness_nogood_selects_distinct_candidate():
    FirstCandidate = _Candidate(((10, 3, 10), (11, 3, 10)))
    SecondCandidate = _Candidate(((10, 5, 10), (11, 5, 10)))
    Problem = _Problem(Foreign=(
        _Domain(
            "Foreign",
            (10, 3, 10),
            "foreign-target",
            FirstCandidate,
            SecondCandidate,
        ),
    ))
    First = SolveComponentRoutingProblem(Problem)
    assert First.Feasible and First.Template is not None
    Selected = First.Template.ForeignEscapeReservations[0][2]
    Second = SolveComponentRoutingProblem(
        Problem,
        ForbiddenForeignCandidateFingerprintsBySignal={
            "Foreign": frozenset((
                Selected.CandidateFingerprint,
            )),
        },
    )
    assert Second.Feasible and Second.Template is not None
    assert (
        Second.Template.ForeignEscapeReservations[0][
            2
        ].CandidateFingerprint
        != Selected.CandidateFingerprint
    )


def test_foreign_pair_nogood_changes_only_conflicting_combination():
    FirstDomain = _Domain(
        "ForeignA",
        (10, 3, 10),
        "foreign-target",
        _Candidate(((10, 3, 10), (11, 3, 10))),
        _Candidate(((10, 5, 10), (11, 5, 10))),
    )
    SecondDomain = _Domain(
        "ForeignB",
        (20, 3, 20),
        "foreign-target",
        _Candidate(((20, 3, 20), (21, 3, 20))),
        _Candidate(((20, 5, 20), (21, 5, 20))),
    )
    Problem = _Problem(Foreign=(FirstDomain, SecondDomain))
    First = SolveComponentRoutingProblem(Problem)
    assert First.Feasible and First.Template is not None
    SelectedPair = frozenset(
        (
            Signal,
            Terminal,
            Candidate.CandidateFingerprint,
        )
        for Signal, Terminal, Candidate
        in First.Template.ForeignEscapeReservations
    )

    Second = SolveComponentRoutingProblem(
        Problem,
        ForbiddenForeignAssignmentPairs=(SelectedPair,),
    )

    assert Second.Feasible and Second.Template is not None
    SecondPair = frozenset(
        (
            Signal,
            Terminal,
            Candidate.CandidateFingerprint,
        )
        for Signal, Terminal, Candidate
        in Second.Template.ForeignEscapeReservations
    )
    assert SecondPair != SelectedPair
    assert any(
        FirstValue in SecondPair
        for FirstValue in SelectedPair
    )


def test_incomplete_classification_and_rename_invariance():
    IncompleteFabric = BuildComponentRoutingFabric(
        _Channel((
            (0, 7, 0),
            (1, 7, 0),
            (1, 7, 1),
            (0, 7, 1),
            (0, 7, 0),
        ))
    )
    Incomplete = SolveComponentRoutingProblem(
        _Problem(Fabric=IncompleteFabric)
    )
    assert Incomplete.Status == "incomplete"
    assert not Incomplete.Exhaustive

    First = SolveComponentRoutingProblem(_Problem("Alpha"))
    Renamed = SolveComponentRoutingProblem(_Problem("Renamed"))
    assert First.Feasible and Renamed.Feasible
    assert First.Template is not None and Renamed.Template is not None
    assert (
        First.Template.RoutedTemplateFingerprint
        == Renamed.Template.RoutedTemplateFingerprint
    )


def load_tests(_Loader, _Tests, _Pattern):
    """Expose compact function tests to the repository's unittest runner."""
    return unittest.TestSuite(
        unittest.FunctionTestCase(Value)
        for Name, Value in sorted(globals().items())
        if Name.startswith("test_") and callable(Value)
    )
