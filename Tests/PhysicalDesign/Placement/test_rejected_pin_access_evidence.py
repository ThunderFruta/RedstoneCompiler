"""Independent contracts for immutable rejected pin-access explanations."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from types import SimpleNamespace

import pytest

import PhysicalDesign.Placement.Access.Catalog as PinAccessCatalog
from Compilation.Ir.Models import Gate, GateKind
from PhysicalDesign.Contracts.PlacementAccess import (
    FrozenPhysicalPlacementContract,
    PlacementAccessCellTransform,
    PlacementAccessConflictCore,
    PlacedPinAccessPatternAttempt,
    PlacedPinAccessOptionDomain,
    PlacedPinAccessRejectionFact,
    PlacementAccessEnvelope,
    PlacementAccessEvaluationControls,
    PlacementAccessPinMapping,
    PlacementAccessPatternAttemptReason,
    PlacementAccessPatternAttemptStatus,
    PlacementAccessRejectionCompleteness,
    PlacementAccessRejectionOwnerProvenance,
    PlacementAccessRejectionRelation,
    PlacementAccessRejectionStatus,
    PlacementAccessSolveResult,
    PlacementAccessSolveStatus,
    PlacementAccessUnavailableInformation,
    SelectedPlacementPinAccessWitness,
)
from PhysicalDesign.Geometry.Placement import BuildPlacedGate
from PhysicalDesign.Placement.Access.Capacity import (
    SolvePlacedPinAccessOptionDomains,
)
from PhysicalDesign.Placement.Access.Catalog import (
    EnumeratePlacedPinAccessOptionDomains,
)
from PhysicalDesign.Placement.Access.Validation import (
    ValidateCurrentSelectedPlacementAccess,
)
from PhysicalDesign.Contracts.PlacementAccess import (
    CurrentSelectedPlacementAccessValidationReason,
    CurrentSelectedPlacementAccessValidationStatus,
)
from PhysicalDesign.Resources.ResourceGraph import (
    RoutingResourceGraph,
    RoutingResourceId,
    RoutingResourceKind,
)
from PhysicalDesign.Redstone.Rules.Geometry import BuildRoutingResources
from PhysicalDesign.Redstone.Technology import (
    DefaultRedstoneRoutingTechnology,
)


Technology = DefaultRedstoneRoutingTechnology


def _InputSource(
    Name="InputSource",
    Output="A",
    Origin=(0, 1, 0),
):
    return BuildPlacedGate(
        Gate(Name, GateKind.INPUT, [Output], []),
        *Origin,
        0,
        False,
    )


def _OutputTarget(
    Name="OutputTarget",
    Input="A",
    Origin=(10, 1, 10),
):
    return BuildPlacedGate(
        Gate(Name, GateKind.OUTPUT, [], [Input]),
        *Origin,
        0,
        False,
    )


def _Resources(Gates):
    Placed = SimpleNamespace(PlacedGates=list(Gates))
    return BuildRoutingResources(Placed).ResourceGraph


def _CompactBytes(Value: object) -> bytes:
    return json.dumps(
        Value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _Enumerate(Gates, Graph, Frozen=None, WorkCheck=None):
    return EnumeratePlacedPinAccessOptionDomains(
        Gates,
        ResourceGraph=Graph,
        Technology=Technology,
        EnabledPatternFamilies=("straight",),
        PreOwnedNodesBySignal=Frozen or {},
        WorkCheck=WorkCheck,
    )


def _ForeignStaticFixture(*, ReverseGates=False):
    SourceA = _InputSource(
        Name="SourceA",
        Output="A",
        Origin=(0, 1, 0),
    )
    SourceB = _InputSource(
        Name="SourceB",
        Output="B",
        Origin=(1, 1, 3),
    )
    Gates = (SourceB, SourceA) if ReverseGates else (SourceA, SourceB)
    Graph = _Resources(Gates)
    Domains = _Enumerate(Gates, Graph)
    RejectedDomain = next(
        Value for Value in Domains if Value.Signal == "A"
    )
    RejectedAttempt, = RejectedDomain.PatternAttempts
    return Gates, Graph, Domains, RejectedDomain, RejectedAttempt


def _SelfClaimFixture(*, MutableFrozen=False):
    Source = _InputSource(
        Name="SourceA",
        Output="A",
        Origin=(0, 1, 0),
    )
    Base = _Resources((Source,))
    PreOwnedPosition = (0, 2, 4)
    Graph = RoutingResourceGraph(
        ActualBlocks=Base.ActualBlocks,
        ElectricalBlocks=Base.ElectricalBlocks | {PreOwnedPosition},
        SolidBlocks=Base.SolidBlocks,
        Technology=Base.Technology,
        GraphVersion=Base.GraphVersion,
        StaticKeepOutBlocks=Base.StaticKeepOutBlocks,
        BlockStates=Base.BlockStates,
    )
    Positions = [PreOwnedPosition] if MutableFrozen else (PreOwnedPosition,)
    Frozen = {"A": Positions}
    Domains = _Enumerate((Source,), Graph, Frozen)
    Domain, = Domains
    Attempt, = Domain.PatternAttempts
    return Source, Graph, Frozen, Domains, Domain, Attempt


def _Controls(Domain, Solve):
    return PlacementAccessEvaluationControls(
        EnabledPatternFamilies=Domain.EnabledPatternFamilies,
        CatalogVersion=Domain.CatalogVersion,
        MaximumGenerationWork=Domain.MaximumGenerationWork,
        MaximumAssignmentExpansions=Solve.MaximumExpansions,
    )


def _FeasiblePortableFixture():
    Gates = (
        _InputSource(
            Name="Source",
            Output="A",
            Origin=(0, 1, 0),
        ),
        _OutputTarget(
            Name="Target",
            Input="A",
            Origin=(10, 1, 10),
        ),
    )
    Graph = _Resources(Gates)
    Domains = _Enumerate(Gates, Graph)
    Solve = SolvePlacedPinAccessOptionDomains(Domains)
    assert Solve.Status is PlacementAccessSolveStatus.Feasible
    assert Solve.SelectedWitness is not None
    Witness = Solve.SelectedWitness
    Contract = FrozenPhysicalPlacementContract(
        ModuleFingerprint="module",
        PolicyVersion="test-physical-policy",
        CatalogVersion=Witness.CatalogVersion,
        TechnologyFingerprint=Witness.TechnologyFingerprint,
        ResourceModelFingerprint=Witness.ResourceModelFingerprint,
        ProblemFingerprint=Solve.ProblemFingerprint,
        ProofFingerprint="proof",
        CellTransforms=tuple(sorted((
            PlacementAccessCellTransform(
                GateName=GateValue.Name,
                GateKind=GateValue.Kind,
                Origin=(GateValue.X, GateValue.Y, GateValue.Z),
                Rotation=GateValue.Rotation,
                MirrorX=GateValue.MirrorX,
            )
            for GateValue in Gates
        ), key=lambda Value: Value.StructuralIdentity())),
        PinMappings=tuple(sorted((
            PlacementAccessPinMapping(
                GateName=Selection.GateName,
                Signal=Selection.Signal,
                Role=Selection.Role,
                LogicalPinId=Selection.PinId,
                PhysicalPinId=Selection.PinId,
            )
            for Selection in Witness.Selections
        ), key=lambda Value: Value.StructuralIdentity())),
        SelectedPinAccessWitness=Witness,
        BoundaryLeases=(),
        ChannelReservations=(),
        Envelope=PlacementAccessEnvelope(
            Minimum=(0, 1, 0),
            Maximum=(20, 4, 20),
        ),
        DomainComplete=True,
        SearchComplete=Solve.SearchComplete,
        OptimalityProven=Solve.OptimalityProven,
    )
    return Domains, Solve, Witness, Contract


def _DowngradeHistoricalAccessRecord(Value):
    if isinstance(Value, list):
        return [
            _DowngradeHistoricalAccessRecord(Item)
            for Item in Value
        ]
    if not isinstance(Value, dict):
        return deepcopy(Value)
    Result = {
        Key: _DowngradeHistoricalAccessRecord(Item)
        for Key, Item in Value.items()
    }
    Schema = Result.get("SchemaVersion")
    if Schema == "placed-pin-access-pattern-attempt-v2":
        Result.pop("SchemaVersion")
        Result.pop("RejectionEvidenceStatus")
        Result.pop("RejectionEvidence")
    elif Schema == "placed-pin-access-option-domain-v4":
        Result["SchemaVersion"] = "placed-pin-access-option-domain-v3"
    elif Schema == "selected-placement-pin-access-witness-v2":
        Result["SchemaVersion"] = "selected-placement-pin-access-witness-v1"
    elif Schema == "placement-access-solve-result-v2":
        Result["SchemaVersion"] = "placement-access-solve-result-v1"
    elif Schema == "frozen-physical-placement-contract-v2":
        Result["SchemaVersion"] = "frozen-physical-placement-contract-v1"
    if "CoreFingerprint" in Result and "ProblemDomains" in Result:
        Result.pop("RejectionEvidenceStatus", None)
        Result.pop("RejectionBlockingResources", None)
    return Result


def _ContainsReconstructedEvidence(Value):
    if isinstance(Value, list):
        return any(_ContainsReconstructedEvidence(Item) for Item in Value)
    if not isinstance(Value, dict):
        return False
    return (
        "RejectionEvidence" in Value
        or any(_ContainsReconstructedEvidence(Item) for Item in Value.values())
    )


def test_foreign_static_rejection_retains_known_signal_without_inventing_provenance():
    _Gates, _Graph, Domains, Domain, Attempt = _ForeignStaticFixture()

    assert Domain.Options == ()
    assert Attempt.Status is PlacementAccessPatternAttemptStatus.Rejected
    assert Attempt.Reason is (
        PlacementAccessPatternAttemptReason.ForeignStaticExclusion
    )
    assert Attempt.OptionFingerprint is None
    assert Attempt.RejectionEvidenceStatus is (
        PlacementAccessRejectionStatus.Partial
    )

    Evidence = Attempt.RejectionEvidence
    assert Evidence is not None
    assert Evidence.Scope == "FirstDecisivePredicate"
    assert Evidence.AttemptId == Attempt.AttemptId
    assert Evidence.EvaluationInputFingerprint == Domain.EvaluationInputFingerprint
    assert Evidence.Terminal == (0, 1, 3)
    assert Evidence.Face == (0, 0, 1)
    assert Evidence.BridgePosition == (0, 1, 2)
    assert Evidence.FirstLegNodes == (
        (0, 1, 3),
        (0, 1, 4),
        (0, 1, 5),
    )
    assert Evidence.FirstTrackNode == (0, 1, 6)
    assert Evidence.BlockRoles == (
        ((0, 1, 3), "dust"),
        ((0, 1, 4), "repeater"),
        ((0, 1, 5), "dust"),
    )
    assert Evidence.ProposedClaims is not None
    assert Evidence.ProposedClaims.WireCells == frozenset(
        Evidence.FirstLegNodes
    )
    Fact = Evidence.Fact
    assert Fact.Relation is (
        PlacementAccessRejectionRelation.ElectricalInfluence
    )
    assert Fact.Position == (0, 1, 3)
    assert Fact.ConflictingResources == (
        RoutingResourceId(
            RoutingResourceKind.Electrical,
            (0, 1, 3),
        ),
    )
    Owner, = Fact.Owners
    assert Owner.Signal == "B"
    assert Owner.Provenance is (
        PlacementAccessRejectionOwnerProvenance.Unavailable
    )
    assert Owner.SourcePosition is None
    assert Owner.GateName is None
    assert Owner.StaticRole is None
    assert Evidence.UnavailableInformation == (
        PlacementAccessUnavailableInformation.OwnerProvenance,
    )

    Solve = SolvePlacedPinAccessOptionDomains(Domains)
    assert Solve.Status is PlacementAccessSolveStatus.Unsatisfiable
    assert Solve.ConflictCore is not None
    assert Solve.ConflictCore.Complete is True
    assert Solve.ConflictCore.BlockingResources == ()
    assert Solve.ConflictCore.RejectionEvidenceStatus is (
        PlacementAccessRejectionStatus.Partial
    )
    assert Solve.ConflictCore.RejectionBlockingResources == (
        "Electrical:0,1,3",
    )


def test_foreign_static_rejection_never_retraverses_static_role_sources(
    monkeypatch,
):
    SourceA = _InputSource(
        Name="SourceA",
        Output="A",
        Origin=(0, 1, 0),
    )
    SourceB = _InputSource(
        Name="SourceB",
        Output="B",
        Origin=(1, 1, 3),
    )
    Gates = (SourceA, SourceB)
    Graph = _Resources(Gates)
    OriginalBuilder = PinAccessCatalog._BuildPlacedStaticExclusionOwnership
    OwnersByPosition, UnownedPositions = OriginalBuilder(
        Gates,
        ResourceGraph=Graph,
        Technology=Technology,
        PreOwnedNodesBySignal={},
    )

    class StaticRoleTraversalGuard:
        def __init__(self, Values):
            self.Values = Values
            self.Armed = False
            self.PostDecisionTraversals = 0

        def __iter__(self):
            if self.Armed:
                self.PostDecisionTraversals += 1
                raise AssertionError(
                    "static roles were traversed after decisive rejection"
                )
            return iter(self.Values)

    class MacroProxy:
        def __init__(self, Target, Guard):
            self.Target = Target
            self.StaticSignalRoles = Guard

        def __getattr__(self, Name):
            return getattr(self.Target, Name)

    Guard = StaticRoleTraversalGuard(
        PinAccessCatalog.CellMacros["INPUT"].StaticSignalRoles
    )

    class ArmOnOwnerLookup(dict):
        def get(self, Key, Default=None):
            Result = super().get(Key, Default)
            if Result:
                Guard.Armed = True
            return Result

    def SupplyIndexedOwnership(*_Arguments, **_Keywords):
        return ArmOnOwnerLookup(OwnersByPosition), UnownedPositions

    monkeypatch.setitem(
        PinAccessCatalog.CellMacros,
        "INPUT",
        MacroProxy(PinAccessCatalog.CellMacros["INPUT"], Guard),
    )
    monkeypatch.setattr(
        PinAccessCatalog,
        "_BuildPlacedStaticExclusionOwnership",
        SupplyIndexedOwnership,
    )

    Domains = _Enumerate(Gates, Graph)
    Attempt, = next(
        Domain for Domain in Domains if Domain.Signal == "A"
    ).PatternAttempts

    assert Attempt.Reason is (
        PlacementAccessPatternAttemptReason.ForeignStaticExclusion
    )
    assert Attempt.RejectionEvidenceStatus is (
        PlacementAccessRejectionStatus.Partial
    )
    assert Guard.PostDecisionTraversals == 0


def test_self_claim_rejection_retains_support_wire_roles_and_real_provenance():
    _Source, _Graph, _Frozen, Domains, Domain, Attempt = _SelfClaimFixture()

    assert Domain.Options == ()
    assert Attempt.Status is PlacementAccessPatternAttemptStatus.Rejected
    assert Attempt.Reason is PlacementAccessPatternAttemptReason.SelfClaimConflict
    assert Attempt.OptionFingerprint is None
    Evidence = Attempt.RejectionEvidence
    assert Evidence is not None
    assert Evidence.Completeness is PlacementAccessRejectionCompleteness.Complete
    Fact = Evidence.Fact
    assert Fact.Relation is PlacementAccessRejectionRelation.Support
    assert Fact.Position == (0, 1, 4)
    assert Fact.ProposedResources == (
        RoutingResourceId(RoutingResourceKind.Wire, (0, 1, 4)),
    )
    assert Fact.ConflictingResources == (
        RoutingResourceId(RoutingResourceKind.Support, (0, 1, 4)),
    )
    assert tuple(
        (Owner.Signal, Owner.Provenance)
        for Owner in Fact.Owners
    ) == (
        (
            "A",
            PlacementAccessRejectionOwnerProvenance.
            PreOwnedFrozenRouteClaims,
        ),
        (
            "A",
            PlacementAccessRejectionOwnerProvenance.ProposedAccess,
        ),
    )
    assert all(
        Owner.SourcePosition is None
        and Owner.GateName is None
        and Owner.StaticRole is None
        for Owner in Fact.Owners
    )

    Solve = SolvePlacedPinAccessOptionDomains(Domains)
    assert Solve.Status is PlacementAccessSolveStatus.Unsatisfiable
    assert Solve.ConflictCore is not None
    assert Solve.ConflictCore.BlockingResources == ()
    assert Solve.ConflictCore.RejectionBlockingResources == (
        "Support:0,1,4",
    )


def test_occupancy_electrical_support_and_air_relations_remain_distinct():
    Position = (7, 2, -3)
    Facts = (
        PlacedPinAccessRejectionFact(
            Relation=PlacementAccessRejectionRelation.Occupancy,
            Position=Position,
            RelatedPosition=None,
            ProposedResources=(RoutingResourceId(
                RoutingResourceKind.Wire,
                Position,
            ),),
            ConflictingResources=(),
            Owners=(),
        ),
        PlacedPinAccessRejectionFact(
            Relation=PlacementAccessRejectionRelation.ElectricalInfluence,
            Position=Position,
            RelatedPosition=None,
            ProposedResources=(RoutingResourceId(
                RoutingResourceKind.Wire,
                Position,
            ),),
            ConflictingResources=(RoutingResourceId(
                RoutingResourceKind.Electrical,
                Position,
            ),),
            Owners=(),
        ),
        PlacedPinAccessRejectionFact(
            Relation=PlacementAccessRejectionRelation.Support,
            Position=Position,
            RelatedPosition=None,
            ProposedResources=(RoutingResourceId(
                RoutingResourceKind.Wire,
                Position,
            ),),
            ConflictingResources=(RoutingResourceId(
                RoutingResourceKind.Support,
                Position,
            ),),
            Owners=(),
        ),
        PlacedPinAccessRejectionFact(
            Relation=PlacementAccessRejectionRelation.RequiredAir,
            Position=Position,
            RelatedPosition=None,
            ProposedResources=(RoutingResourceId(
                RoutingResourceKind.Wire,
                Position,
            ),),
            ConflictingResources=(RoutingResourceId(
                RoutingResourceKind.Air,
                Position,
            ),),
            Owners=(),
        ),
    )

    assert tuple(Fact.ToDictionary()["Relation"] for Fact in Facts) == (
        "Occupancy",
        "ElectricalInfluence",
        "Support",
        "RequiredAir",
    )
    assert tuple(
        tuple(
            Resource["Kind"]
            for Resource in Fact.ToDictionary()["ConflictingResources"]
        )
        for Fact in Facts
    ) == ((), ("Electrical",), ("Support",), ("Air",))


def test_unavailable_owner_and_partial_evidence_never_claim_complete():
    Source = _InputSource(
        Name="SourceA",
        Output="A",
        Origin=(0, 1, 0),
    )
    Base = _Resources((Source,))
    Terminal = (0, 1, 3)
    Blocked = RoutingResourceGraph(
        ActualBlocks=Base.ActualBlocks | {Terminal},
        ElectricalBlocks=Base.ElectricalBlocks,
        SolidBlocks=Base.SolidBlocks | {Terminal},
        Technology=Base.Technology,
        GraphVersion=Base.GraphVersion,
        StaticKeepOutBlocks=Base.StaticKeepOutBlocks,
        BlockStates=Base.BlockStates,
    )
    Domain, = _Enumerate((Source,), Blocked)
    Attempt, = Domain.PatternAttempts

    Evidence = Attempt.RejectionEvidence
    assert Evidence is not None
    assert Attempt.RejectionEvidenceStatus is PlacementAccessRejectionStatus.Partial
    assert Evidence.Completeness is PlacementAccessRejectionCompleteness.Partial
    assert Evidence.ProposedClaims is None
    assert set(Evidence.UnavailableInformation) == {
        PlacementAccessUnavailableInformation.ProposedClaims,
        PlacementAccessUnavailableInformation.ConflictingClaims,
        PlacementAccessUnavailableInformation.ConflictingOwner,
        PlacementAccessUnavailableInformation.OwnerProvenance,
    }
    assert Evidence.Fact.Owners == ()
    with pytest.raises(ValueError, match="overclaims"):
        replace(
            Evidence,
            Completeness=PlacementAccessRejectionCompleteness.Complete,
        )

    UnavailableAttempt = replace(Attempt, RejectionEvidence=None)
    assert UnavailableAttempt.RejectionEvidenceStatus is (
        PlacementAccessRejectionStatus.Unavailable
    )
    UnavailableDomain = replace(
        Domain,
        PatternAttempts=(UnavailableAttempt,),
    )
    Solve = SolvePlacedPinAccessOptionDomains((UnavailableDomain,))
    assert Solve.Status is PlacementAccessSolveStatus.Unsatisfiable
    assert Solve.ConflictCore is not None
    assert Solve.ConflictCore.RejectionEvidenceStatus is (
        PlacementAccessRejectionStatus.Unavailable
    )
    assert Solve.ConflictCore.RejectionBlockingResources == ()


def test_partial_pattern_evaluation_remains_incomplete_without_rejection_evidence():
    Source = _InputSource(
        Name="SourceA",
        Output="A",
        Origin=(0, 1, 0),
    )
    Graph = _Resources((Source,))
    Domain, = EnumeratePlacedPinAccessOptionDomains(
        (Source,),
        ResourceGraph=Graph,
        Technology=Technology,
        EnabledPatternFamilies=("planar-jog", "straight"),
        MaximumGenerationWork=1,
    )

    assert Domain.Complete is False
    assert Domain.IncompleteReason == "catalog-domain-generation-work-cap"
    assert tuple(
        Attempt.Status for Attempt in Domain.PatternAttempts
    ).count(PlacementAccessPatternAttemptStatus.NotEvaluated) == 2
    assert all(
        Attempt.RejectionEvidence is None
        and Attempt.RejectionEvidenceStatus
        is PlacementAccessRejectionStatus.NotApplicable
        for Attempt in Domain.PatternAttempts
        if Attempt.Status is PlacementAccessPatternAttemptStatus.NotEvaluated
    )
    Solve = SolvePlacedPinAccessOptionDomains((Domain,))
    assert Solve.Status is PlacementAccessSolveStatus.Incomplete
    assert Solve.SearchComplete is False
    assert Solve.ConflictCore is None


def test_rejection_evidence_detaches_mutable_sources_and_decoded_json():
    Source, Graph, Frozen, _Domains, Domain, Attempt = _SelfClaimFixture(
        MutableFrozen=True,
    )
    Before = deepcopy(Attempt.ToDictionary())
    BeforeFingerprint = Attempt.RejectionEvidence.EvidenceFingerprint

    Source.OutputPin = (100, 1, 100)
    Frozen["A"].append((9, 9, 9))

    assert Attempt.ToDictionary() == Before
    assert Attempt.RejectionEvidence.EvidenceFingerprint == BeforeFingerprint
    with pytest.raises(FrozenInstanceError):
        Attempt.RejectionEvidence.Scope = "AllConflicts"

    Payload = json.loads(json.dumps(Before))
    Decoded = PlacedPinAccessPatternAttempt.FromDictionary(Payload)
    Payload["RejectionEvidence"]["FirstLegNodes"][0][0] += 50
    Payload["RejectionEvidence"]["Fact"]["Position"][0] += 50
    assert Decoded == Attempt

    Solve = SolvePlacedPinAccessOptionDomains((Domain,))
    Controls = _Controls(Domain, Solve)
    Validation = ValidateCurrentSelectedPlacementAccess(
        (replace(Source, OutputPin=(0, 1, 3)),),
        None,
        Solve,
        ResourceGraph=Graph,
        Technology=Technology,
        FrozenNetWires={"A": ((0, 2, 4),)},
        CurrentControls=Controls,
    )
    assert Validation.Status is (
        CurrentSelectedPlacementAccessValidationStatus.Unresolved
    )
    assert Validation.Reason is (
        CurrentSelectedPlacementAccessValidationReason.UnsatisfiableSolve
    )


def test_rejection_evidence_is_canonical_under_gate_and_input_permutation():
    _Gates, _Graph, FirstDomains, _Domain, FirstAttempt = (
        _ForeignStaticFixture()
    )
    _Gates, _Graph, SecondDomains, _Domain, SecondAttempt = (
        _ForeignStaticFixture(ReverseGates=True)
    )
    _Gates, _Graph, ThirdDomains, _Domain, ThirdAttempt = (
        _ForeignStaticFixture()
    )

    assert tuple(Value.ToDictionary() for Value in FirstDomains) == tuple(
        Value.ToDictionary() for Value in SecondDomains
    ) == tuple(Value.ToDictionary() for Value in ThirdDomains)
    assert FirstAttempt.AttemptId == SecondAttempt.AttemptId == ThirdAttempt.AttemptId
    assert (
        FirstAttempt.RejectionEvidence.EvidenceFingerprint
        == SecondAttempt.RejectionEvidence.EvidenceFingerprint
        == ThirdAttempt.RejectionEvidence.EvidenceFingerprint
    )


def test_codec_rejects_stale_corrupt_or_reordered_rejection_evidence():
    _Source, _Graph, _Frozen, _Domains, _Domain, Attempt = _SelfClaimFixture()
    Payload = json.loads(json.dumps(Attempt.ToDictionary()))
    assert PlacedPinAccessPatternAttempt.FromDictionary(Payload) == Attempt

    Corruptions = []
    MissingEvidenceField = deepcopy(Payload)
    del MissingEvidenceField["RejectionEvidence"]
    Corruptions.append(MissingEvidenceField)
    StaleAttempt = deepcopy(Payload)
    StaleAttempt["RejectionEvidence"]["AttemptId"] = "stale"
    Corruptions.append(StaleAttempt)
    StaleInput = deepcopy(Payload)
    StaleInput["RejectionEvidence"]["EvaluationInputFingerprint"] = "stale"
    Corruptions.append(StaleInput)
    BadResource = deepcopy(Payload)
    BadResource["RejectionEvidence"]["Fact"]["ConflictingResources"][0][
        "Kind"
    ] = "Occupancy"
    Corruptions.append(BadResource)
    DuplicateResource = deepcopy(Payload)
    DuplicateResource["RejectionEvidence"]["Fact"][
        "ConflictingResources"
    ].append(deepcopy(
        DuplicateResource["RejectionEvidence"]["Fact"][
            "ConflictingResources"
        ][0]
    ))
    Corruptions.append(DuplicateResource)
    ReorderedOwners = deepcopy(Payload)
    ReorderedOwners["RejectionEvidence"]["Fact"]["Owners"].reverse()
    Corruptions.append(ReorderedOwners)
    FalseComplete = deepcopy(Payload)
    FalseComplete["RejectionEvidence"]["UnavailableInformation"] = [
        "ConflictingOwner"
    ]
    Corruptions.append(FalseComplete)

    for Corrupt in Corruptions:
        with pytest.raises(ValueError):
            PlacedPinAccessPatternAttempt.FromDictionary(Corrupt)


def test_historical_decoders_never_reconstruct_rejection_evidence():
    _Source, _Graph, _Frozen, RejectedDomains, RejectedDomain, Attempt = (
        _SelfClaimFixture()
    )
    RejectedSolve = SolvePlacedPinAccessOptionDomains(RejectedDomains)
    assert RejectedSolve.ConflictCore is not None
    _FeasibleDomains, _FeasibleSolve, Witness, FrozenContract = (
        _FeasiblePortableFixture()
    )
    Cases = (
        (
            "attempt",
            PlacedPinAccessPatternAttempt.FromDictionary,
            Attempt,
        ),
        (
            "domain",
            PlacedPinAccessOptionDomain.FromDictionary,
            RejectedDomain,
        ),
        (
            "conflict-core",
            PlacementAccessConflictCore.FromDictionary,
            RejectedSolve.ConflictCore,
        ),
        (
            "selected-witness",
            SelectedPlacementPinAccessWitness.FromDictionary,
            Witness,
        ),
        (
            "solve-result",
            PlacementAccessSolveResult.FromDictionary,
            RejectedSolve,
        ),
        (
            "frozen-contract",
            FrozenPhysicalPlacementContract.FromDictionary,
            FrozenContract,
        ),
    )

    for Name, Decoder, Current in Cases:
        CurrentPayload = json.loads(json.dumps(Current.ToDictionary()))
        assert Decoder(CurrentPayload) == Current, Name
        HistoricalPayload = _DowngradeHistoricalAccessRecord(CurrentPayload)
        Before = deepcopy(HistoricalPayload)
        assert not _ContainsReconstructedEvidence(HistoricalPayload), Name
        with pytest.raises(ValueError):
            Decoder(HistoricalPayload)
        assert HistoricalPayload == Before, Name
        assert not _ContainsReconstructedEvidence(HistoricalPayload), Name


def test_evidence_preserves_baseline_admission_identities_counts_and_classification():
    _Gates, _Graph, ForeignDomains, _Domain, ForeignAttempt = (
        _ForeignStaticFixture()
    )
    _Source, _Graph, _Frozen, SelfDomains, _Domain, SelfAttempt = (
        _SelfClaimFixture()
    )

    assert ForeignAttempt.AttemptId == "da4fcc5c23998658"
    assert SelfAttempt.AttemptId == "c44dc22b911d4346"
    assert [
        Option.PlacedBindingFingerprint
        for Domain in ForeignDomains
        for Option in Domain.Options
    ] == ["3feb863c59bc2e7f"]
    assert tuple(
        (
            len(Domains),
            sum(len(Domain.PatternAttempts) for Domain in Domains),
            sum(Domain.GeneratedOptionCount for Domain in Domains),
            sum(Domain.RejectedOptionCount for Domain in Domains),
            sum(Domain.DeduplicatedOptionCount for Domain in Domains),
        )
        for Domains in (ForeignDomains, SelfDomains)
    ) == (
        (2, 2, 1, 1, 0),
        (1, 1, 0, 1, 0),
    )
    for Domains in (ForeignDomains, SelfDomains):
        Solve = SolvePlacedPinAccessOptionDomains(Domains)
        assert Solve.Status is PlacementAccessSolveStatus.Unsatisfiable
        assert Solve.SearchComplete is True
        assert Solve.SelectedWitness is None
        assert Solve.ConflictCore is not None

    assert len(_CompactBytes(ForeignAttempt.ToDictionary())) <= 4_096
    assert len(_CompactBytes(SelfAttempt.ToDictionary())) <= 4_096


def test_actual_serializer_and_fingerprint_versions_are_explicit():
    _Gates, _Graph, ForeignDomains, _Domain, Attempt = _ForeignStaticFixture()
    Solve = SolvePlacedPinAccessOptionDomains(ForeignDomains)

    assert Attempt.SchemaVersion == "placed-pin-access-pattern-attempt-v2"
    assert Attempt.RejectionEvidence.SchemaVersion == (
        "placed-pin-access-rejection-evidence-v1"
    )
    assert all(
        Domain.SchemaVersion == "placed-pin-access-option-domain-v4"
        for Domain in ForeignDomains
    )
    assert Solve.SchemaVersion == "placement-access-solve-result-v2"
    assert Solve.ConflictCore is not None
    assert Solve.ConflictCore.CoreFingerprint
