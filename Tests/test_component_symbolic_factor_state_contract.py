"""Contract tests for the physical local-factor symbolic state fast path."""

import pytest
from dataclasses import replace
from types import SimpleNamespace

from Compiler.Routing.Components.SymbolicWorkers import (
    CompilePreparedComponentPhysicalFactorStateBatch,
    CompilePreparedComponentSymbolicNetStates,
)
from Compiler.Routing.Components.SymbolicState import (
    PrepareComponentSymbolicNetStateContext,
)
from Tests.test_component_router import _Claims, _Problem


def _WithPhysicalEgress(Base, LocalPath, *, IncludeLocalClaims):
    Certified = tuple(
        Candidate.CandidateFingerprint
        for Domain in Base.OwnedTerminalDomains
        for Candidate in Domain.Candidates
    )
    PortValues = {
        "Signal": "Alpha",
        "Direction": "output",
        "FabricDomainFingerprint": "fabric-alpha",
        "FabricAttachment": LocalPath[0],
        "Attachment": LocalPath[-1],
        "LocalPath": tuple(LocalPath),
        "OwnedTerminals": tuple(
            Domain.Terminal for Domain in Base.OwnedTerminalDomains
        ),
        "OwnedTerminalFingerprints": (),
        "OwnedCandidateFingerprints": Certified,
        "OwnedAccessCandidates": (),
        "Capacity": 1,
    }
    if IncludeLocalClaims:
        PortValues["LocalClaims"] = _Claims(*LocalPath)
    return replace(
        Base,
        Interface=SimpleNamespace(
            Complete=True,
            DeclaredFeedthroughSignals=frozenset(),
            PhysicalPortReservations=(SimpleNamespace(**PortValues),),
        ),
    )


def test_incremental_physical_factor_state_matches_authoritative_fallback():
    Base = _Problem(External=(("Alpha", (3, 7, 0), "target"),))
    LocalPath = ((2, 7, 0), (3, 7, 0))
    IncrementalProblem = _WithPhysicalEgress(
        Base,
        LocalPath,
        IncludeLocalClaims=True,
    )
    FallbackProblem = _WithPhysicalEgress(
        Base,
        LocalPath,
        IncludeLocalClaims=False,
    )

    Incremental = CompilePreparedComponentSymbolicNetStates(
        PrepareComponentSymbolicNetStateContext(
            IncrementalProblem,
            "Alpha",
        ),
        IncrementalProblem,
        SymbolicNetStateCache={},
    )
    Fallback = CompilePreparedComponentSymbolicNetStates(
        PrepareComponentSymbolicNetStateContext(
            FallbackProblem,
            "Alpha",
        ),
        FallbackProblem,
        SymbolicNetStateCache={},
    )

    assert Incremental.Complete and Fallback.Complete
    assert Incremental.States == Fallback.States
    assert Incremental.CacheKey == Fallback.CacheKey


def test_physical_factor_state_cache_key_keeps_exact_egress_identity():
    Base = _Problem(External=(("Alpha", (3, 7, 0), "target"),))
    FirstProblem = _WithPhysicalEgress(
        Base,
        ((2, 7, 0), (3, 7, 0)),
        IncludeLocalClaims=True,
    )
    SecondProblem = _WithPhysicalEgress(
        Base,
        ((2, 7, 0), (2, 7, 1)),
        IncludeLocalClaims=True,
    )
    Context = PrepareComponentSymbolicNetStateContext(
        FirstProblem,
        "Alpha",
    )
    Cache = {}

    First = CompilePreparedComponentSymbolicNetStates(
        Context,
        FirstProblem,
        SymbolicNetStateCache=Cache,
    )
    Second = CompilePreparedComponentSymbolicNetStates(
        Context,
        SecondProblem,
        SymbolicNetStateCache=Cache,
    )

    assert First.Complete and Second.Complete
    assert First.CacheKey != Second.CacheKey
    assert First.States != Second.States
    assert len(Cache) == 2
    assert Context.TerminalFrontierBuildCount == 1
    assert Context.TerminalFrontierCacheHitCount == 1


def test_physical_factor_batch_matches_individual_exact_domains():
    Base = _Problem(External=(("Alpha", (3, 7, 0), "target"),))
    Problems = {
        "first": _WithPhysicalEgress(
            Base,
            ((2, 7, 0), (3, 7, 0)),
            IncludeLocalClaims=True,
        ),
        "second": _WithPhysicalEgress(
            Base,
            ((2, 7, 0), (2, 7, 1)),
            IncludeLocalClaims=True,
        ),
    }
    Individual = {
        Name: CompilePreparedComponentSymbolicNetStates(
            PrepareComponentSymbolicNetStateContext(Problem, "Alpha"),
            Problem,
            SymbolicNetStateCache={},
        )
        for Name, Problem in Problems.items()
    }
    Context = PrepareComponentSymbolicNetStateContext(
        Problems["first"],
        "Alpha",
    )
    Cache = {}

    Batch = CompilePreparedComponentPhysicalFactorStateBatch(
        Context,
        Problems,
        SymbolicNetStateCache=Cache,
    )

    assert set(Batch) == set(Problems)
    assert all(Value.Complete for Value in Batch.values())
    assert {
        Name: Value.States for Name, Value in Batch.items()
    } == {
        Name: Value.States for Name, Value in Individual.items()
    }
    assert {
        Name: Value.CacheKey for Name, Value in Batch.items()
    } == {
        Name: Value.CacheKey for Name, Value in Individual.items()
    }
    assert len(Cache) == 2
    assert Context.TerminalFrontierBuildCount == 1


def test_physical_factor_batch_ignores_reordered_domain_and_external_inputs():
    Base = _Problem(
        External=(
            ("Alpha", (3, 7, 0), "target"),
            ("Alpha", (4, 7, 0), "source"),
        )
    )
    ReorderedProblem = replace(
        Base,
        OwnedTerminalDomains=tuple(reversed(Base.OwnedTerminalDomains)),
        ExternalContinuationTerminals=tuple(
            reversed(Base.ExternalContinuationTerminals)
        ),
    )
    BaseWithPort = _WithPhysicalEgress(
        Base,
        ((2, 7, 0), (3, 7, 0)),
        IncludeLocalClaims=True,
    )
    ReorderedWithPort = _WithPhysicalEgress(
        ReorderedProblem,
        ((2, 7, 0), (3, 7, 0)),
        IncludeLocalClaims=True,
    )
    Context = PrepareComponentSymbolicNetStateContext(
        BaseWithPort,
        "Alpha",
    )
    Cache = {}
    Batch = CompilePreparedComponentPhysicalFactorStateBatch(
        Context,
        {
            "base": BaseWithPort,
            "reordered": ReorderedWithPort,
        },
        SymbolicNetStateCache=Cache,
    )

    assert Batch["base"].Complete
    assert Batch["reordered"].Complete
    assert Batch["base"].States == Batch["reordered"].States


def test_physical_factor_batch_fails_on_semantically_different_context():
    Base = _Problem(External=(("Alpha", (3, 7, 0), "target"),))
    Divergent = _Problem(
        External=(("Alpha", (3, 7, 0), "target"),),
        MaximumPowerDistance=99,
    )
    BaseWithPort = _WithPhysicalEgress(
        Base,
        ((2, 7, 0), (3, 7, 0)),
        IncludeLocalClaims=True,
    )
    DivergentWithPort = _WithPhysicalEgress(
        Divergent,
        ((2, 7, 0), (3, 7, 0)),
        IncludeLocalClaims=True,
    )
    Context = PrepareComponentSymbolicNetStateContext(
        BaseWithPort,
        "Alpha",
    )

    with pytest.raises(ValueError, match="prepared physical factor batch identity mismatch"):
        CompilePreparedComponentPhysicalFactorStateBatch(
            Context,
            {
                "base": BaseWithPort,
                "divergent": DivergentWithPort,
            },
            SymbolicNetStateCache={},
        )
