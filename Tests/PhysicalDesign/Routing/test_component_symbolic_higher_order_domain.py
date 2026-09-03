from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest

import PhysicalDesign.Routing.Regions.Proofs.Certification as ComponentCertification
import PhysicalDesign.Routing.Regions.Symbolic.SymbolicDomains as PhysicalSymbolicDomains
from PhysicalDesign.Routing.Regions.Symbolic.SymbolicDomains import CompilePhysicalComponentSymbolicHigherOrderDomain, CompilePhysicalComponentSymbolicPortPairDomain, ProjectCompletePhysicalHigherOrderCertificateToApertureClauses, ValidatePhysicalComponentSymbolicHigherOrderCertificate
from PhysicalDesign.Contracts.Component import PhysicalComponentPortReservation
from PhysicalDesign.Resources.ResourceGraph import RoutingResourceClaims


@dataclass(frozen=True)
class _Interface:
    InterfaceFingerprint: str
    PhysicalPortReservations: tuple
    PhysicalAssemblyPlanFingerprint: str = ""


@dataclass(frozen=True)
class _Problem:
    ProblemFingerprint: str
    PlacementFingerprint: str
    Fabric: object
    Interface: _Interface
    ReservedGlobalClaimsBySignal: tuple = ()
    ComponentSignals: tuple[str, ...] = ()
    PhysicalAssemblyPlan: object | None = None
    OwnedTerminalDomains: tuple = ()
    LocalClaims: tuple = ()
    ImmutableClaims: tuple = ()
    ExternalContinuationTerminals: tuple = ()
    MaximumPowerDistance: int = 15
    ResourceGraph: object = None


def _Claims(*Cells):
    return RoutingResourceClaims(WireCells=frozenset(Cells))


def _State(Name, Claims):
    return SimpleNamespace(
        Signal=Name[0],
        NetFingerprint=Name,
        Nodes=frozenset(),
        Edges=frozenset(),
        Claims=Claims,
        CoveredTerminals=(),
        ExportedPorts=(),
        RepeaterInputFacings=(),
    )


def _Fixture(monkeypatch):
    Signals = ("Alpha", "Beta", "Gamma")
    Empty = RoutingResourceClaims()
    Ports = tuple(
        PhysicalComponentPortReservation(
            Signal=Signal,
            Direction="output",
            OwnedTerminals=((Index, 0, 0),),
            OwnedTerminalFingerprints=(f"terminal-{Signal}",),
            OwnedCandidateFingerprints=(f"candidate-{Signal}",),
            FabricDomainFingerprint=f"fabric-{Signal}",
            FabricAttachment=(Index, 0, 1),
            Attachment=(Index, 0, 2),
            LocalPath=((Index, 0, 1), (Index, 0, 2)),
            GlobalPath=((Index, 0, 2),),
            Claims=Empty,
            LocalClaims=Empty,
            GlobalClaims=Empty,
        )
        for Index, Signal in enumerate(Signals)
    )
    Problem = _Problem(
        ProblemFingerprint="problem",
        PlacementFingerprint="placement",
        Fabric=SimpleNamespace(FabricFingerprint="fabric"),
        Interface=_Interface("interface", Ports),
        ComponentSignals=Signals,
    )
    Factors = tuple(
        SimpleNamespace(
            Signal=Signal,
            Direction="output",
            Capacity=1,
            OwnedTerminals=Port.OwnedTerminals,
            OwnedTerminalFingerprints=Port.OwnedTerminalFingerprints,
            OwnedAccessCandidates=(),
            OwnedCandidateFingerprints=Port.OwnedCandidateFingerprints,
            FabricDomainFingerprint=Port.FabricDomainFingerprint,
            FabricAttachment=Port.FabricAttachment,
            LocalPath=Port.LocalPath,
            LocalClaims=Empty,
            LocalContractFingerprint=f"local-contract-{Signal}",
            LocalAccessFingerprint=f"access-{Signal}",
            SeamContractFingerprint=f"seam-{Signal}",
        )
        for Signal, Port in zip(Signals, Ports)
    )
    Supports = tuple(
        SimpleNamespace(
            Signal=Signal,
            LocalAccessFingerprint=Factor.LocalAccessFingerprint,
            ApertureOptionFingerprint=f"aperture-{Signal}",
            SupportFingerprint=f"support-{Signal}",
        )
        for Signal, Factor in zip(Signals, Factors)
    )
    Apertures = tuple(
        SimpleNamespace(
            Signal=Signal,
            ApertureOptionFingerprint=f"aperture-{Signal}",
            ApertureContractFingerprint=f"aperture-contract-{Signal}",
        )
        for Signal in Signals
    )
    FactorDomain = SimpleNamespace(
        Complete=True,
        Feasible=True,
        DomainFingerprint="prepared",
        PlacementFingerprint="placement",
        ComponentGraphFingerprint="component-graph",
        ResourceGraphFingerprint="resource-graph",
        AccessCertificate=SimpleNamespace(
            TechnologyFingerprint="technology",
            CertificateFingerprint="access-certificate",
        ),
        Problem=Problem,
        LocalAccessFactorsBySignal=tuple(
            (Signal, (Factor,))
            for Signal, Factor in zip(Signals, Factors)
        ),
        LocalApertureSupportBySignal=tuple(
            (Signal, (Support,))
            for Signal, Support in zip(Signals, Supports)
        ),
        ApertureFactorsBySignal=tuple(
            (Signal, (Aperture,))
            for Signal, Aperture in zip(Signals, Apertures)
        ),
        LocalApertureSupportsByOption=tuple(
            (
                (Signal, Aperture.ApertureOptionFingerprint),
                (Support,),
            )
            for Signal, Aperture, Support
            in zip(Signals, Apertures, Supports)
        ),
    )

    # Every pair has a witness, but the equality/equality/inequality cycle has
    # no jointly compatible triple.
    StateDomains = {
        "Alpha": (
            _State("Alpha-0", _Claims((0, 0, 0), (4, 0, 0))),
            _State("Alpha-1", _Claims((1, 0, 0), (5, 0, 0))),
        ),
        "Beta": (
            _State("Beta-0", _Claims((1, 0, 0), (2, 0, 0))),
            _State("Beta-1", _Claims((0, 0, 0), (3, 0, 0))),
        ),
        "Gamma": (
            _State("Gamma-0", _Claims((3, 0, 0), (4, 0, 0))),
            _State("Gamma-1", _Claims((2, 0, 0), (5, 0, 0))),
        ),
    }

    monkeypatch.setattr(
        PhysicalSymbolicDomains,
        "PrepareComponentSymbolicNetStateContext",
        lambda _Problem, Signal, **_KeywordArgs: Signal,
    )
    monkeypatch.setattr(
        PhysicalSymbolicDomains,
        "_BuildPreparedComponentSymbolicNetStateContextFingerprint",
        lambda _Problem, Signal: Signal,
    )
    monkeypatch.setattr(
        ComponentCertification,
        "_BuildPreparedComponentSymbolicNetStateContextFingerprint",
        lambda _Problem, Signal: Signal,
    )

    def CompileBatch(Context, Problems, **_KeywordArgs):
        return {
            Access: SimpleNamespace(
                Complete=True,
                States=StateDomains[Context],
                CacheKey=(
                    PhysicalSymbolicDomains.BuildComponentSymbolicNetStateCacheKey(
                        VariantProblem,
                        Context,
                        PreparedContextFingerprint=Context,
                    )
                ),
            )
            for Access, VariantProblem in Problems.items()
        }

    monkeypatch.setattr(
        PhysicalSymbolicDomains,
        "CompilePreparedComponentPhysicalFactorStateBatch",
        CompileBatch,
    )
    return Problem, FactorDomain, Signals


def test_higher_order_certificate_records_no_support_for_unsat_tuple(
    monkeypatch,
):
    Problem, FactorDomain, Signals = _Fixture(monkeypatch)

    Certificate = CompilePhysicalComponentSymbolicHigherOrderDomain(
        Problem,
        FactorDomain,
        Signals,
        DeadlineSeconds=None,
    )

    assert Certificate.Complete is True
    assert Certificate.SupportedLocalAccessTuples == ()
    assert Certificate.SupportedSeamTuples == ()
    assert Certificate.CompatibilityCheckCount > 0


def test_pair_certificate_uses_complete_indexed_component_relation(
    monkeypatch,
):
    Problem, FactorDomain, Signals = _Fixture(monkeypatch)
    Events = []

    Certificate = CompilePhysicalComponentSymbolicPortPairDomain(
        Problem,
        FactorDomain,
        Signals[:2],
        DeadlineSeconds=None,
        WorkCheck=Events.append,
    )

    assert Certificate.Complete is True
    assert Certificate.UnsupportedLocalAccessPairs == ((
        ("Alpha", "access-Alpha"),
        ("Beta", "access-Beta"),
    ),)
    assert Certificate.UnsupportedSeamPairs == ((
        ("Alpha", "seam-Alpha"),
        ("Beta", "seam-Beta"),
    ),)
    assert any(
        Event.get("CompatibilityIndexKind")
        == "normalized-claim-cell-bitset-v2"
        for Event in Events
    )


def test_pair_complete_relation_deduplicates_access_and_mandatory_states(
    monkeypatch,
):
    Problem, FactorDomain, Signals = _Fixture(monkeypatch)
    OriginalCompileBatch = (
        PhysicalSymbolicDomains
        .CompilePreparedComponentPhysicalFactorStateBatch
    )

    def CompileDuplicateBatch(Context, Problems, **KeywordArgs):
        Compilations = OriginalCompileBatch(
            Context,
            Problems,
            **KeywordArgs,
        )
        return {
            Access: SimpleNamespace(
                **{
                    **vars(Compilation),
                    "States": tuple(Compilation.States) * 3,
                }
            )
            for Access, Compilation in Compilations.items()
        }

    monkeypatch.setattr(
        PhysicalSymbolicDomains,
        "CompilePreparedComponentPhysicalFactorStateBatch",
        CompileDuplicateBatch,
    )
    Events = []
    Certificate = CompilePhysicalComponentSymbolicPortPairDomain(
        Problem,
        FactorDomain,
        Signals[:2],
        DeadlineSeconds=None,
        WorkCheck=Events.append,
    )

    assert Certificate.Complete is True
    assert len(Certificate.UnsupportedLocalAccessPairs) == 1
    IndexedEvents = [
        Event for Event in Events
        if Event.get("CompatibilityIndexKind")
        == "normalized-claim-cell-bitset-v2"
    ]
    assert IndexedEvents
    assert max(
        Event["CompatibilityCheckCount"] for Event in IndexedEvents
    ) <= 24


def test_higher_order_projection_requires_every_supported_seam_tuple(
    monkeypatch,
):
    Problem, FactorDomain, Signals = _Fixture(monkeypatch)
    Certificate = CompilePhysicalComponentSymbolicHigherOrderDomain(
        Problem,
        FactorDomain,
        Signals,
        DeadlineSeconds=None,
    )
    Clauses, Diagnostics = (
        ProjectCompletePhysicalHigherOrderCertificateToApertureClauses(
            FactorDomain,
            Certificate,
        )
    )
    ExpectedClause = frozenset(
        (Signal, f"aperture-contract-{Signal}")
        for Signal in Signals
    )
    assert Clauses == frozenset((ExpectedClause,))
    assert Diagnostics["HigherOrderApertureProjectionComplete"] is True

    AlphaFactor = dict(
        FactorDomain.LocalAccessFactorsBySignal
    )["Alpha"][0]
    AlternateAlphaFactor = SimpleNamespace(
        **{
            **vars(AlphaFactor),
            "LocalAccessFingerprint": "access-Alpha-alternate",
            "SeamContractFingerprint": "seam-Alpha-alternate",
        }
    )
    AlphaSupport = dict(
        FactorDomain.LocalApertureSupportsByOption
    )[("Alpha", "aperture-Alpha")][0]
    AlternateAlphaSupport = SimpleNamespace(
        **{
            **vars(AlphaSupport),
            "LocalAccessFingerprint": "access-Alpha-alternate",
            "SupportFingerprint": "support-Alpha-alternate",
        }
    )
    ExpandedFactors = tuple(
        (
            Signal,
            (*Factors, AlternateAlphaFactor)
            if Signal == "Alpha"
            else Factors,
        )
        for Signal, Factors in FactorDomain.LocalAccessFactorsBySignal
    )
    ExpandedSupports = tuple(
        (
            Key,
            (*Supports, AlternateAlphaSupport)
            if Key == ("Alpha", "aperture-Alpha")
            else Supports,
        )
        for Key, Supports in FactorDomain.LocalApertureSupportsByOption
    )
    ExpandedDomain = SimpleNamespace(
        **{
            **vars(FactorDomain),
            "LocalAccessFactorsBySignal": ExpandedFactors,
            "LocalApertureSupportsByOption": ExpandedSupports,
        }
    )
    ExpandedCertificate = replace(
        Certificate,
        LocalAccessFingerprintsBySignal=tuple(
            (
                Signal,
                (*Accesses, "access-Alpha-alternate")
                if Signal == "Alpha"
                else Accesses,
            )
            for Signal, Accesses
            in Certificate.LocalAccessFingerprintsBySignal
        ),
        SeamFingerprintByLocalAccess=(
            *Certificate.SeamFingerprintByLocalAccess,
            (
                "Alpha",
                "access-Alpha-alternate",
                "seam-Alpha-alternate",
            ),
        ),
        SeamFingerprintsBySignal=tuple(
            (
                Signal,
                (*Seams, "seam-Alpha-alternate")
                if Signal == "Alpha"
                else Seams,
            )
            for Signal, Seams in Certificate.SeamFingerprintsBySignal
        ),
        SupportedSeamTuples=((
            ("Alpha", "seam-Alpha-alternate"),
            ("Beta", "seam-Beta"),
            ("Gamma", "seam-Gamma"),
        ),),
    )
    PartialClauses, PartialDiagnostics = (
        ProjectCompletePhysicalHigherOrderCertificateToApertureClauses(
            ExpandedDomain,
            ExpandedCertificate,
        )
    )
    assert PartialClauses == frozenset()
    assert PartialDiagnostics[
        "HigherOrderApertureProjectionComplete"
    ] is True

    FullyUnsupportedCertificate = replace(
        ExpandedCertificate,
        SupportedSeamTuples=(),
    )
    FullClauses, _FullDiagnostics = (
        ProjectCompletePhysicalHigherOrderCertificateToApertureClauses(
            ExpandedDomain,
            FullyUnsupportedCertificate,
        )
    )
    assert FullClauses == frozenset((ExpectedClause,))


def test_higher_order_projection_can_restrict_to_current_plan_contracts(
    monkeypatch,
):
    Problem, FactorDomain, Signals = _Fixture(monkeypatch)
    Certificate = CompilePhysicalComponentSymbolicHigherOrderDomain(
        Problem,
        FactorDomain,
        Signals,
        DeadlineSeconds=None,
    )
    AlphaAperture = dict(FactorDomain.ApertureFactorsBySignal)[
        "Alpha"
    ][0]
    AliasAperture = SimpleNamespace(
        **{
            **vars(AlphaAperture),
            "ApertureOptionFingerprint": "aperture-Alpha-alias",
            "ApertureContractFingerprint": (
                "aperture-contract-Alpha-alias"
            ),
        }
    )
    AlphaSupport = dict(
        FactorDomain.LocalApertureSupportsByOption
    )[("Alpha", "aperture-Alpha")][0]
    AliasSupport = SimpleNamespace(
        **{
            **vars(AlphaSupport),
            "ApertureOptionFingerprint": "aperture-Alpha-alias",
            "SupportFingerprint": "support-Alpha-alias",
        }
    )
    ExpandedDomain = SimpleNamespace(
        **{
            **vars(FactorDomain),
            "ApertureFactorsBySignal": tuple(
                (
                    Signal,
                    (*Apertures, AliasAperture)
                    if Signal == "Alpha"
                    else Apertures,
                )
                for Signal, Apertures
                in FactorDomain.ApertureFactorsBySignal
            ),
            "LocalApertureSupportsByOption": (
                *FactorDomain.LocalApertureSupportsByOption,
                (("Alpha", "aperture-Alpha-alias"), (AliasSupport,)),
            ),
        }
    )

    Clauses, Diagnostics = (
        ProjectCompletePhysicalHigherOrderCertificateToApertureClauses(
            ExpandedDomain,
            Certificate,
            RestrictedApertureContractsBySignal={
                "Alpha": "aperture-contract-Alpha-alias",
                "Beta": "aperture-contract-Beta",
                "Gamma": "aperture-contract-Gamma",
            },
        )
    )

    assert Clauses == frozenset((frozenset((
        ("Alpha", "aperture-contract-Alpha-alias"),
        ("Beta", "aperture-contract-Beta"),
        ("Gamma", "aperture-contract-Gamma"),
    )),))
    assert Diagnostics["HigherOrderApertureProjectionRestricted"] is True
    assert (
        Diagnostics["HigherOrderApertureProjectionContractTupleCount"]
        == 1
    )


def test_higher_order_projection_rejects_incomplete_or_ambiguous_mapping(
    monkeypatch,
):
    Problem, FactorDomain, Signals = _Fixture(monkeypatch)
    Certificate = CompilePhysicalComponentSymbolicHigherOrderDomain(
        Problem,
        FactorDomain,
        Signals,
        DeadlineSeconds=None,
    )
    AlphaSupports = dict(
        FactorDomain.LocalApertureSupportsByOption
    )[("Alpha", "aperture-Alpha")]
    MismatchedSupport = SimpleNamespace(
        **{
            **vars(AlphaSupports[0]),
            "ApertureOptionFingerprint": "different-option",
        }
    )
    MismatchedDomain = SimpleNamespace(
        **{
            **vars(FactorDomain),
            "LocalApertureSupportsByOption": tuple(
                (
                    Key,
                    (MismatchedSupport,)
                    if Key == ("Alpha", "aperture-Alpha")
                    else Supports,
                )
                for Key, Supports
                in FactorDomain.LocalApertureSupportsByOption
            ),
        }
    )

    MismatchedClauses, MismatchedDiagnostics = (
        ProjectCompletePhysicalHigherOrderCertificateToApertureClauses(
            MismatchedDomain,
            Certificate,
        )
    )
    assert MismatchedClauses == frozenset()
    assert MismatchedDiagnostics[
        "HigherOrderApertureProjectionComplete"
    ] is False
    assert MismatchedDiagnostics[
        "HigherOrderApertureProjectionFailureReason"
    ] == "prepared-support-access-unresolved"

    AmbiguousCertificate = replace(
        Certificate,
        SeamFingerprintByLocalAccess=(
            *Certificate.SeamFingerprintByLocalAccess,
            ("Alpha", "access-Alpha", "different-seam"),
        ),
    )
    AmbiguousClauses, AmbiguousDiagnostics = (
        ProjectCompletePhysicalHigherOrderCertificateToApertureClauses(
            FactorDomain,
            AmbiguousCertificate,
        )
    )
    assert AmbiguousClauses == frozenset()
    assert AmbiguousDiagnostics[
        "HigherOrderApertureProjectionComplete"
    ] is False
    assert AmbiguousDiagnostics[
        "HigherOrderApertureProjectionFailureReason"
    ] == "certificate-access-seam-map-incomplete-or-ambiguous"


def test_higher_order_completed_cache_is_validated_and_reused(monkeypatch):
    Problem, FactorDomain, Signals = _Fixture(monkeypatch)
    Cache = {}
    First = CompilePhysicalComponentSymbolicHigherOrderDomain(
        Problem,
        FactorDomain,
        Signals,
        DeadlineSeconds=None,
        CompletedCertificateCache=Cache,
    )

    Second = CompilePhysicalComponentSymbolicHigherOrderDomain(
        Problem,
        FactorDomain,
        reversed(Signals),
        DeadlineSeconds=None,
        CompletedCertificateCache=Cache,
    )

    assert Second is First
    ChangedProblem = replace(Problem, PlacementFingerprint="changed")
    with pytest.raises(ValueError, match="placement identity mismatch"):
        CompilePhysicalComponentSymbolicHigherOrderDomain(
            ChangedProblem,
            FactorDomain,
            Signals,
            DeadlineSeconds=None,
            CompletedCertificateCache=Cache,
        )


def test_higher_order_certificate_rejects_tampered_proof_identity(monkeypatch):
    Problem, FactorDomain, Signals = _Fixture(monkeypatch)
    Certificate = CompilePhysicalComponentSymbolicHigherOrderDomain(
        Problem,
        FactorDomain,
        Signals,
        DeadlineSeconds=None,
    )

    with pytest.raises(ValueError, match="ProofFingerprint"):
        ValidatePhysicalComponentSymbolicHigherOrderCertificate(
            replace(Certificate, ProofFingerprint="tampered"),
            Problem,
            FactorDomain,
            Signals,
        )


def test_incomplete_higher_order_compilation_emits_no_positive_tuple_proof(
    monkeypatch,
):
    Problem, FactorDomain, Signals = _Fixture(monkeypatch)
    monkeypatch.setattr(
        PhysicalSymbolicDomains,
        "CompilePreparedComponentPhysicalFactorStateBatch",
        lambda Context, Problems, **_KeywordArgs: {
            Access: SimpleNamespace(
                Complete=False,
                States=None,
                CacheKey=f"incomplete-{Context}-{Access}",
            )
            for Access in Problems
        },
    )
    Cache = {}

    Certificate = CompilePhysicalComponentSymbolicHigherOrderDomain(
        Problem,
        FactorDomain,
        Signals,
        DeadlineSeconds=0.0,
        CompletedCertificateCache=Cache,
    )

    assert Certificate.Complete is False
    assert Certificate.SupportedLocalAccessTuples == ()
    assert Certificate.SupportedSeamTuples == ()
    assert Cache == {}
