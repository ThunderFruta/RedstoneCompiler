"""Production-boundary challenges for current typed routing receipt admission."""

from dataclasses import dataclass, replace
from time import monotonic

import pytest

from PhysicalDesign.Contracts.Failures import RoutingStageError
from PhysicalDesign.Resources.ResourceGraph import RoutingResourceClaims
from PhysicalDesign.Runtime.NativeRouting import NativeRouteResultKind
from PhysicalDesign.Orchestration.Runner import PlaceAndRoutePcb
import PhysicalDesign.Routing.Global.Orchestration.Flow as Flow
import PhysicalDesign.Routing.Global.Orchestration.Stages.CandidatePreparation as Preparation
import PhysicalDesign.Routing.Global.Orchestration.Stages.CandidateMaterialization as Materialization
from Tests.Integration.test_candidate_preparation_results import BuildSingleNandNetlist, BuildThreeNandChainNetlist


def ObserveCurrentState(monkeypatch):
    Current = []
    Original = Flow.RunAuthoritativeRoutingPhases

    def Run(State, Services, Phases=None):
        Current.append((State, Services))
        try:
            if Phases is None:
                return Original(State, Services)
            return Original(State, Services, Phases)
        finally:
            Current.pop()

    monkeypatch.setattr(Flow, "RunAuthoritativeRoutingPhases", Run)
    return Current


@pytest.mark.parametrize("Mutation", (
    "policy", "selected-access", "region", "placed", "occupancy", "deadline",
    "selected-claim-map", "sibling-map", "coarse-plan", "expansion-cap", "request",
))
def test_native_success_with_changed_preparation_never_reaches_materializer(
    monkeypatch, Mutation,
):
    Current = ObserveCurrentState(monkeypatch)
    Original = Preparation.ExecuteNativeRouteBatchOutcomesV1
    OriginalMaterialize = Preparation.ValidateTypedRouteExecutionScope
    Changed = []
    Accepted = []
    StaleFailures = []

    def Execute(*Args, **Kwargs):
        Result = Original(*Args, **Kwargs)
        if not Changed and any(Value.Kind is NativeRouteResultKind.Routed for Value in Result.Results):
            State, _Services = Current[-1]
            if Mutation == "policy":
                State.Policy = replace(State.Policy, DetailedRouting=replace(
                    State.Policy.DetailedRouting,
                    LengthPenalty=State.Policy.DetailedRouting.LengthPenalty + 1,
                ))
            elif Mutation == "selected-access":
                State.PlacementPinAccessWitness = None
            elif Mutation == "region":
                State.Region = replace(State.Region, Nodes=State.Region.Nodes | {(900, 1, 900)})
            elif Mutation == "placed":
                Gates = list(State.Placed.PlacedGates)
                Gates[0] = replace(Gates[0], X=Gates[0].X + 100)
                State.Placed = replace(State.Placed, PlacedGates=Gates)
            elif Mutation == "occupancy":
                State.Resources.ResourceGraph.ActualBlocks |= {(900, 1, 900)}
            elif Mutation == "deadline":
                State.AdaptiveExpiresAt = min(State.Deadline.ExpiresAt, State.AdaptiveExpiresAt) - 0.001
            elif Mutation == "selected-claim-map":
                State.ForeignSelectedPinAccessClaimsBySignal["foreign"] = ()
            elif Mutation == "sibling-map":
                State.AssemblySpecificSiblingAperturesBySignal["foreign"] = ()
            elif Mutation == "request":
                Request = next(Values[0] for Values in State.RouteRequestsBySignal.values() if Values)
                Request[3].append((900, 1, 900))
            elif Mutation == "expansion-cap":
                State.CandidateExpansionLimits = {
                    Signal: Cap + 1 for Signal, Cap in State.CandidateExpansionLimits.items()
                }
            else:
                assert Mutation == "coarse-plan"
                if State.CoarsePlan is None:
                    State.CoarsePlan = {"Guides": {"foreign": ((900, 900),)}}
                else:
                    State.CoarsePlan.Guides["foreign"] = frozenset({(900, 900)})
            Changed.append(Mutation)
        return Result

    def Validate(State, Services, Scope, Descriptors):
        try:
            OriginalMaterialize(State, Services, Scope, Descriptors)
        except RoutingStageError as Error:
            StaleFailures.append(Error.Failure)
            raise
        if Changed:
            Accepted.append(Scope)

    monkeypatch.setattr(Preparation, "ExecuteNativeRouteBatchOutcomesV1", Execute)
    monkeypatch.setattr(Preparation, "ValidateTypedRouteExecutionScope", Validate)
    with pytest.raises(RoutingStageError) as Error:
        PlaceAndRoutePcb(BuildSingleNandNetlist(), Strategy="routing-aware-placement-access")
    assert Changed == [Mutation]
    assert Accepted == []
    assert StaleFailures
    assert all(Failure.Stage == "TypedRouteReceiptPublication" for Failure in StaleFailures)
    assert all(Failure.Diagnostics["Complete"] is False for Failure in StaleFailures)
    assert all(Failure.Diagnostics["Action"] == "reject-stale-typed-route-preparation" for Failure in StaleFailures)


def test_real_typed_and_legacy_success_keep_identical_native_geometry(monkeypatch):
    OriginalBuilder = Preparation.BuildTypedNativeCoarseRequest
    Original = Preparation.ExecuteNativeRouteBatchOutcomesV1
    LegacyByRequest = {}
    Comparisons = []

    def Build(RequestId, Bindings, Bounds, PlacementBounds, Request, **Keywords):
        LegacyByRequest[RequestId] = Request
        return OriginalBuilder(RequestId, Bindings, Bounds, PlacementBounds, Request, **Keywords)

    def Execute(Context, BatchIdentity, Requests, DeadlineAt, *, Detailed):
        Result = Original(Context, BatchIdentity, Requests, DeadlineAt, Detailed=Detailed)
        Legacy = Context.GenerateRouteTreesBounded(
            [LegacyByRequest[Request.RequestId] for Request in Requests],
            max(0, int((DeadlineAt - monotonic()) * 1000)),
        )
        for Typed, Old in zip(Result.Results, Legacy.RouteTrees):
            if Typed.Kind is NativeRouteResultKind.Routed:
                assert Old is not None
                assert set(Typed.Candidate.Nodes) == set(Old)
                Comparisons.append(Typed.RequestIdentity)
        return Result

    monkeypatch.setattr(Preparation, "BuildTypedNativeCoarseRequest", Build)
    monkeypatch.setattr(Preparation, "ExecuteNativeRouteBatchOutcomesV1", Execute)
    Result = PlaceAndRoutePcb(BuildSingleNandNetlist(), Strategy="routing-aware-placement-access")
    assert Comparisons
    assert Result.Routed.RouteCandidateCount > 0


class _ObservedPhysicalRejection(BaseException):
    """Stop after the real producer and physical predicate have both run."""


@pytest.mark.parametrize("Conflict", ("destination-wire", "support", "air", "electrical-neighbor"))
def test_native_success_does_not_override_current_physical_claims(monkeypatch, Conflict):
    OriginalSnapshot = Preparation.BuildTypedRouteCurrentCallerSnapshot
    OriginalExecute = Preparation.ExecuteNativeRouteBatchOutcomesV1
    OriginalAdmission = Materialization.TypedRouteAdmissionRecord
    Injected = []
    NativeSuccesses = []
    Rejections = []

    def Snapshot(State, Context, Descriptors):
        if not Injected:
            Descriptor = Descriptors[0]
            Signal = Descriptor.Signal
            Profile = State.Profiles[Signal]
            Position = (
                Profile.TargetAccessPaths[Profile.Targets[0]][-1]
                if Conflict == "destination-wire" else Profile.SourceAccessPath[-1]
            )
            X, Y, Z = Position
            if Conflict == "destination-wire":
                Claims = State.Resources.ResourceGraph.BuildRouteClaims({Position})
                ExpectedKind = "Electrical"
            elif Conflict == "support":
                Position = (X, Y - 1, Z)
                Claims = RoutingResourceClaims(WireCells=frozenset({Position}))
                ExpectedKind = "Support"
            elif Conflict == "air":
                Claims = RoutingResourceClaims(RequiredAirCells=frozenset({Position}))
                ExpectedKind = "Air"
            else:
                Position = (X + 1, Y, Z)
                Claims = State.Resources.ResourceGraph.BuildRouteClaims({Position})
                ExpectedKind = "Electrical"
            State.ForeignSelectedPinAccessClaimsBySignal[Signal] = (
                *State.ForeignSelectedPinAccessClaimsBySignal[Signal],
                ("foreign-owner", Claims),
            )
            Injected.append((Signal, Position, ExpectedKind))
        return OriginalSnapshot(State, Context, Descriptors)

    def Execute(*Arguments, **Keywords):
        Result = OriginalExecute(*Arguments, **Keywords)
        NativeSuccesses.extend(Value for Value in Result.Results if Value.Kind is NativeRouteResultKind.Routed)
        return Result

    def Admission(*Arguments, **Keywords):
        Record = OriginalAdmission(*Arguments, **Keywords)
        Evidence = Record.PhysicalEvidence
        if Evidence is not None and Evidence.Signal == Injected[0][0]:
            assert Record.Admitted is False
            assert Evidence.Reason == "ForeignSelectedAccessConflict"
            assert "foreign-owner" in Evidence.ConflictingOwners
            assert any(Resource["Kind"] == Injected[0][2] for Resource in Evidence.ConflictResources)
            Rejections.append(Record)
            raise _ObservedPhysicalRejection()
        return Record

    monkeypatch.setattr(Preparation, "BuildTypedRouteCurrentCallerSnapshot", Snapshot)
    monkeypatch.setattr(Preparation, "ExecuteNativeRouteBatchOutcomesV1", Execute)
    monkeypatch.setattr(Materialization, "TypedRouteAdmissionRecord", Admission)
    with pytest.raises(_ObservedPhysicalRejection):
        PlaceAndRoutePcb(BuildSingleNandNetlist(), Strategy="routing-aware-placement-access")
    assert NativeSuccesses
    assert len(Rejections) == 1


@pytest.mark.parametrize("Kind", ("SearchLimitIncomplete", "CancellationIncomplete"))
@pytest.mark.parametrize("RecoveryMode", ("staged", "component", "foreign-escape"))
def test_real_incomplete_receipts_never_enter_physical_admission_or_starvation(monkeypatch, Kind, RecoveryMode):
    OriginalRun = Flow.RunAuthoritativeRoutingPhases
    OriginalExecute = Preparation.ExecuteNativeRouteBatchOutcomesV1
    OriginalBuilder = Preparation.BuildTypedNativeCoarseRequest
    OriginalPlan = Preparation.BuildTypedRouteExecutionPlan
    States = []
    NativeKinds = []
    PhysicalTrees = []
    PhaseFailures = []

    def Run(State, Services, Phases=None):
        States.append(State)
        ActualPhases = Flow.AUTHORITATIVE_ROUTING_PHASES if Phases is None else Phases
        Wrapped = []
        for Phase in ActualPhases:
            if Phase is Preparation.RunCandidatePreparation:
                def Prepare(StateValue, ServicesValue, OriginalPhase=Phase):
                    Result = OriginalPhase(StateValue, ServicesValue)
                    StateValue.CandidateExpansionLimits = {
                        Signal: 0 for Signal in StateValue.CandidateExpansionLimits
                    } if Kind == "SearchLimitIncomplete" else StateValue.CandidateExpansionLimits
                    # Exercise the real staged scheduler and its otherwise
                    # eligible placement-starvation branch on this tiny fixture.
                    StateValue.ApplyStagedPortfolioProof = True
                    StateValue.ApplyMaturePortfolioSearchCaps = True
                    StateValue.ExactLegalRetainedJointStateCount = 2
                    StateValue.HasPhysicalComponentRoutingContract = RecoveryMode == "component"
                    if RecoveryMode == "foreign-escape":
                        StateValue.HasRoutedComponentTemplate = True
                        StateValue.RoutedComponentForeignEscapeSignals = set(StateValue.Profiles)
                        StateValue.CandidateDiversityLevel = 3
                    return Result
                Wrapped.append(Prepare)
            else:
                def Observe(StateValue, ServicesValue, OriginalPhase=Phase):
                    try:
                        return OriginalPhase(StateValue, ServicesValue)
                    except RoutingStageError as Error:
                        PhaseFailures.append(Error.Failure)
                        raise
                Wrapped.append(Observe)
        return OriginalRun(State, Services, tuple(Wrapped))

    def Build(*Args, **Keywords):
        if Kind == "CancellationIncomplete":
            Keywords["CancellationRequestedBeforeStart"] = True
        return OriginalBuilder(*Args, **Keywords)

    def Plan(*Args, **Keywords):
        if Kind == "CancellationIncomplete":
            Keywords["CancellationRequestedBeforeStart"] = True
        return OriginalPlan(*Args, **Keywords)

    def Execute(*Args, **Keywords):
        Result = OriginalExecute(*Args, **Keywords)
        NativeKinds.extend(Value.Kind.value for Value in Result.Results)
        return Result

    def RejectPhysical(State, Services, Tree):
        PhysicalTrees.append(Tree)
        raise AssertionError("incomplete native work entered physical materialization")

    monkeypatch.setattr(Flow, "RunAuthoritativeRoutingPhases", Run)
    monkeypatch.setattr(Preparation, "BuildTypedNativeCoarseRequest", Build)
    monkeypatch.setattr(Preparation, "BuildTypedRouteExecutionPlan", Plan)
    monkeypatch.setattr(Preparation, "ExecuteNativeRouteBatchOutcomesV1", Execute)
    monkeypatch.setattr(Materialization, "ValidateTypedRouteOriginBeforeMaterialization", RejectPhysical)
    with pytest.raises(RoutingStageError):
        PlaceAndRoutePcb(BuildSingleNandNetlist(), Strategy="routing-aware-placement-access")
    assert NativeKinds and set(NativeKinds) == {Kind}
    assert PhysicalTrees == []
    RoutedStates = [State for State in States if State.TypedNativeRouteBatches]
    assert RoutedStates
    if RecoveryMode == "staged":
        assert any(State.WorkTelemetry.get("MatureStagedInitialCandidateScheduler", {}).get("Applied") for State in RoutedStates)
    assert all(Failure.Diagnostics.get("Action") not in {
        "advance-routed-component-global-starvation",
        "reject-routed-component-foreign-escape",
    } for Failure in PhaseFailures)
    assert all("PortfolioCandidateStarvationAdvance" not in State.WorkTelemetry for State in RoutedStates)
    assert all(Record.Admitted is None for State in RoutedStates for Record in State.TypedNativeRouteAdmissionRecords)
    assert all(not Batch["Counters"]["CompleteScopedNoPath"] for State in RoutedStates for Batch in State.TypedNativeRouteBatches)


def test_staged_seed_rejection_is_admitted_once_without_second_materialization(monkeypatch):
    OriginalRun = Flow.RunAuthoritativeRoutingPhases
    OriginalScheduler = Flow.GenerateStagedInitialRouteTrees
    OriginalSnapshot = Preparation.BuildTypedRouteCurrentCallerSnapshot
    OriginalValidate = Materialization.ValidateTypedRouteOriginBeforeMaterialization
    States = []
    FirstSignal = []
    Visits = []
    Staged = []

    def Run(State, Services, Phases=None):
        States.append(State)
        Actual = Flow.AUTHORITATIVE_ROUTING_PHASES if Phases is None else Phases
        Wrapped = []
        for Phase in Actual:
            if Phase is Preparation.RunCandidatePreparation:
                def Prepare(StateValue, ServicesValue, OriginalPhase=Phase):
                    Result = OriginalPhase(StateValue, ServicesValue)
                    StateValue.ApplyStagedPortfolioProof = True
                    return Result
                Wrapped.append(Prepare)
            else:
                Wrapped.append(Phase)
        return OriginalRun(State, Services, tuple(Wrapped))

    def Scheduler(*Args, **Keywords):
        Keywords["StopAfterEverySignalHasTree"] = True
        Result = OriginalScheduler(*Args, **Keywords)
        Staged.append(Result)
        return Result

    def Snapshot(State, Context, Descriptors):
        if not FirstSignal:
            Signal = Descriptors[0].Signal
            Position = State.Profiles[Signal].SourceAccessPath[-1]
            State.ForeignSelectedPinAccessClaimsBySignal[Signal] = (
                *State.ForeignSelectedPinAccessClaimsBySignal[Signal],
                ("foreign-owner", State.Resources.ResourceGraph.BuildRouteClaims({Position})),
            )
            FirstSignal.append(Signal)
        return OriginalSnapshot(State, Context, Descriptors)

    def Validate(State, Services, Tree):
        Origin = State.TypedNativeRouteNodeOriginRecords.get(id(Tree))
        if Origin is not None:
            Visits.append(Origin.OriginIdentity)
        return OriginalValidate(State, Services, Tree)

    monkeypatch.setattr(Flow, "RunAuthoritativeRoutingPhases", Run)
    monkeypatch.setattr(Flow, "GenerateStagedInitialRouteTrees", Scheduler)
    monkeypatch.setattr(Preparation, "BuildTypedRouteCurrentCallerSnapshot", Snapshot)
    monkeypatch.setattr(Materialization, "ValidateTypedRouteOriginBeforeMaterialization", Validate)
    with pytest.raises(RoutingStageError):
        PlaceAndRoutePcb(BuildSingleNandNetlist(), Strategy="routing-aware-placement-access")
    assert any(Result.EverySignalHasTree and not Result.FullPoolGenerated for Result in Staged)
    assert Visits and len(Visits) == len(set(Visits))
    Rejected = [Record for State in States for Record in (State.TypedNativeRouteAdmissionRecords or ()) if Record.Admitted is False]
    assert Rejected
    assert all(Record.PhysicalEvidence.Reason == "ForeignSelectedAccessConflict" for Record in Rejected)


class _ObservedScopedNegative(BaseException):
    """Stop at the actual request-completion boundary, before materialization."""


@pytest.mark.parametrize("Mutation", ("none", "policy", "request"))
def test_real_scoped_no_path_is_completed_only_under_current_authority(monkeypatch, Mutation):
    OriginalRun = Flow.RunAuthoritativeRoutingPhases
    OriginalExecute = Preparation.ExecuteNativeRouteBatchOutcomesV1
    Current = []
    NativeNoPaths = []
    Completed = []
    Stale = []

    def Execute(*Arguments, **Keywords):
        Result = OriginalExecute(*Arguments, **Keywords)
        NoPaths = [Value for Value in Result.Results if Value.Kind is NativeRouteResultKind.CompleteScopedNoPath]
        NativeNoPaths.extend(NoPaths)
        if NoPaths and Mutation != "none":
            State = Current[-1]
            if Mutation == "request":
                Request = next(Values[0] for Values in State.RouteRequestsBySignal.values() if Values)
                Request[3].append((900, 1, 900))
            else:
                State.Policy = replace(State.Policy, DetailedRouting=replace(
                    State.Policy.DetailedRouting,
                    LengthPenalty=State.Policy.DetailedRouting.LengthPenalty + 1,
                ))
        return Result

    def Run(State, Services, Phases=None):
        Actual = Flow.AUTHORITATIVE_ROUTING_PHASES if Phases is None else Phases
        Wrapped = []
        for Phase in Actual:
            if Phase is Preparation.RunCandidatePreparation:
                def Prepare(StateValue, ServicesValue, OriginalPhase=Phase):
                    Outcome = OriginalPhase(StateValue, ServicesValue)
                    Generate = StateValue.GenerateRouteTreesWithDeadline

                    def GenerateDisconnected(Requests):
                        CutRequests = []
                        for Request in Requests:
                            Values = list(Request)
                            Values[2] = []  # Finite disconnected routing columns
                            Cut = tuple(Values)
                            StateValue.TypedNativeRouteRequestOriginDescriptorsById[id(Cut)] = StateValue.TypedNativeRouteRequestOriginDescriptorsById[id(Request)]
                            StateValue.PhysicalDescriptorOwnerByRequestId[id(Cut)] = StateValue.PhysicalDescriptorOwnerByRequestId[id(Request)]
                            for SignalRequests in StateValue.RouteRequestsBySignal.values():
                                for RequestIndex, CurrentRequest in enumerate(SignalRequests):
                                    if CurrentRequest is Request:
                                        SignalRequests[RequestIndex] = Cut
                            CutRequests.append(Cut)
                        try:
                            Values = Generate(CutRequests)
                        except RoutingStageError as Error:
                            if NativeNoPaths:
                                Stale.append((StateValue, Error.Failure))
                                raise _ObservedScopedNegative()
                            raise
                        if NativeNoPaths:
                            Completed.append(StateValue)
                            raise _ObservedScopedNegative()
                        return Values

                    StateValue.GenerateRouteTreesWithDeadline = GenerateDisconnected
                    return Outcome
                Wrapped.append(Prepare)
            else:
                Wrapped.append(Phase)
        Current.append(State)
        try:
            return OriginalRun(State, Services, tuple(Wrapped))
        finally:
            Current.pop()

    monkeypatch.setattr(Flow, "RunAuthoritativeRoutingPhases", Run)
    monkeypatch.setattr(Preparation, "ExecuteNativeRouteBatchOutcomesV1", Execute)
    with pytest.raises(_ObservedScopedNegative):
        PlaceAndRoutePcb(BuildThreeNandChainNetlist(), Strategy="routing-aware-placement-access")
    assert NativeNoPaths
    assert all(Result.CompleteScopedNoPathProof.Complete is True for Result in NativeNoPaths)
    if Mutation != "none":
        assert not Completed
        assert len(Stale) == 1
        State, Failure = Stale[0]
        assert Failure.Stage == "TypedRouteReceiptPublication"
        assert Failure.Diagnostics["Complete"] is False
        assert not State.CompletedPhysicalDescriptorFingerprintsBySignal
        assert not State.TypedNativeRouteBatches
    else:
        assert not Stale
        assert len(Completed) == 1
        State = Completed[0]
        assert State.CompletedPhysicalDescriptorFingerprintsBySignal
        assert any(Batch["Counters"]["CompleteScopedNoPath"] for Batch in State.TypedNativeRouteBatches)


def test_equivalent_native_requests_preserve_distinct_production_admissions(monkeypatch):
    """Distinct physical variants share native work without sharing admission."""
    from collections import Counter

    OriginalRun = Flow.RunAuthoritativeRoutingPhases
    OriginalSchedulerChoice = Flow.ShouldUseMatureStagedInitialCandidateScheduler
    Current = []
    States = []

    def AddEquivalentVariant(*Arguments, **Keywords):
        State = Current[-1]
        for Signal, Requests in State.RouteRequestsBySignal.items():
            if not Requests:
                continue
            OriginalRequest = Requests[0]
            Equivalent = tuple(list(OriginalRequest))
            Metadata = list(State.RouteMetadataBySignal[Signal][0])
            Metadata[-1] += 10000
            Metadata = tuple(Metadata)
            Requests.insert(1, Equivalent)
            State.RouteMetadataBySignal[Signal].insert(1, Metadata)
            State.TypedNativeRouteRequestOriginDescriptorsById[id(Equivalent)] = (
                Materialization.BuildTypedRouteOriginDescriptor(
                    Signal, State.Profiles[Signal], Metadata,
                )
            )
            State.PhysicalDescriptorOwnerByRequestId[id(Equivalent)] = (
                State.PhysicalDescriptorOwnerByRequestId[id(OriginalRequest)]
            )
        State.InitialRequestLimit = max(2, State.InitialRequestLimit)
        return OriginalSchedulerChoice(*Arguments, **Keywords)

    def Run(State, Services, Phases=None):
        Current.append(State)
        try:
            if Phases is None:
                return OriginalRun(State, Services)
            return OriginalRun(State, Services, Phases)
        finally:
            States.append(State)
            Current.pop()

    monkeypatch.setattr(Flow, "ShouldUseMatureStagedInitialCandidateScheduler", AddEquivalentVariant)
    monkeypatch.setattr(Flow, "RunAuthoritativeRoutingPhases", Run)
    Result = PlaceAndRoutePcb(BuildSingleNandNetlist(), Strategy="routing-aware-placement-access")
    assert Result.Routed.ZeroResourceConflicts is True
    assert any(Origin.EquivalentReused for State in States for Origin in (State.TypedNativeRouteOriginRecords or ()))
    for State in States:
        Routed = {
            Origin.OriginIdentity for Origin in (State.TypedNativeRouteOriginRecords or ())
            if Origin.NativeKind == "Routed"
        }
        Admissions = Counter(
            Record.OriginIdentity for Record in (State.TypedNativeRouteAdmissionRecords or ())
            if Record.NativeKind == "Routed"
        )
        assert set(Admissions) == Routed
        assert all(Count == 1 for Count in Admissions.values())
        for Batch in State.TypedNativeRouteBatches:
            Counts = Batch["Counters"]
            assert Counts["PhysicallyAccepted"] + Counts["PhysicallyRejected"] == Counts["Routed"]
            assert Counts["CanonicalExecuted"] + Counts["EquivalentReused"] == Counts["Materialized"]


def test_authority_observation_owns_nested_values_and_rechecks_opaque_producers():
    from types import SimpleNamespace
    from PhysicalDesign.Routing.Global.Orchestration.TypedRouteAuthority import ObserveTypedRouteAuthorityValue

    Source = {"nested": [{"path": [(1, 2, 3)], "flags": {"first"}}]}
    Before = ObserveTypedRouteAuthorityValue(Source)
    Source["nested"][0]["path"].append((4, 5, 6))
    assert ObserveTypedRouteAuthorityValue(Source) != Before
    Source["nested"][0]["path"].pop()
    assert ObserveTypedRouteAuthorityValue(Source) == Before
    Source["nested"][0]["flags"].add("second")
    assert ObserveTypedRouteAuthorityValue(Source) != Before

    @dataclass(frozen=True)
    class Producer:
        Declared: int = 0

        def ToDictionary(self):
            return {"value": External.value}

    External = SimpleNamespace(value=1)
    Value = Producer()
    OpaqueBefore = ObserveTypedRouteAuthorityValue(Value)
    External.value = 2
    assert ObserveTypedRouteAuthorityValue(Value) != OpaqueBefore


def test_value_equal_caller_snapshot_cache_keeps_exact_canonical_bindings(monkeypatch):
    import PhysicalDesign.Routing.Global.Orchestration.TypedRouteAuthority as Authority

    Original = Authority.BuildTypedRouteCurrentCallerSnapshot
    Comparisons = []

    def CompareCachedWithFresh(State, Scope, Descriptors):
        Cached = Original(State, Scope, Descriptors)
        OtherDescriptors = tuple(replace(Value, Variant=Value.Variant + 1)
                                 for Value in Descriptors)
        Other = Original(State, Scope, OtherDescriptors)
        assert dict(Other.Bindings)["DependencySnapshotIdentity"] != dict(Cached.Bindings)["DependencySnapshotIdentity"]
        assert all(dict(Other.Bindings)[Name] == Identity for Name, Identity in Cached.Bindings
                   if Name != "DependencySnapshotIdentity")
        assert Original(State, Scope, Descriptors) == Cached
        State.TypedNativeCallerSnapshotObservationCache = None
        State.TypedNativeCallerSnapshotsByOrigins = {}
        State.TypedNativeCallerAuthorityObservationCache = {}
        State.TypedNativeImmutableObservationCache = {}
        Fresh = Original(State, Scope, Descriptors)
        assert Cached.Bindings == Fresh.Bindings
        Comparisons.append(Cached.Identity)
        return Cached

    monkeypatch.setattr(Preparation, "BuildTypedRouteCurrentCallerSnapshot", CompareCachedWithFresh)
    monkeypatch.setattr(Authority, "BuildTypedRouteCurrentCallerSnapshot", CompareCachedWithFresh)
    Result = PlaceAndRoutePcb(BuildSingleNandNetlist(), Strategy="routing-aware-placement-access")
    assert Result.Routed.ZeroResourceConflicts is True
    assert Comparisons


def test_authority_observation_preserves_custom_mapping_projection_and_signed_zero():
    from PhysicalDesign.Routing.Global.Orchestration.TypedRouteAuthority import ObserveTypedRouteAuthorityValue

    class CustomMapping(dict):
        def ToDictionary(self):
            return {"projected": self.External}

    Value = CustomMapping(stored=1)
    Value.External = "first"
    Before = ObserveTypedRouteAuthorityValue(Value)
    Value.External = "second"
    assert ObserveTypedRouteAuthorityValue(Value) != Before
    assert ObserveTypedRouteAuthorityValue(-0.0) != ObserveTypedRouteAuthorityValue(0.0)


def test_immutable_observation_cache_certifies_only_exact_immutable_forests():
    from PhysicalDesign.Routing.Global.Orchestration.TypedRouteAuthority import ObserveTypedRouteAuthorityValue

    Cache = {}
    Forest = frozenset((Index, 1, 0) for Index in range(20))
    First = ObserveTypedRouteAuthorityValue(Forest, Cache)
    assert Cache[id(Forest)][0] is Forest
    assert ObserveTypedRouteAuthorityValue(Forest, Cache) is First
    Mutable = [list(Forest)]
    Container = (Mutable, 1, 2, 3)
    Before = ObserveTypedRouteAuthorityValue(Container, Cache)
    assert id(Container) not in Cache and id(Mutable) not in Cache
    Mutable[0].append((99, 1, 0))
    assert ObserveTypedRouteAuthorityValue(Container, Cache) != Before
    assert ObserveTypedRouteAuthorityValue((True, 1, 2), Cache) != ObserveTypedRouteAuthorityValue((1, 1, 2), Cache)


@pytest.mark.parametrize("Mutation", ("policy", "profile", "resource", "selected-claims", "sibling-claims", "coarse-plan", "layer-count", "portal-mode", "metadata", "request"))
@pytest.mark.parametrize("RejectPhysically", (False, True))
def test_frozen_materialization_never_publishes_after_live_input_mutation(
    monkeypatch, Mutation, RejectPhysically,
):
    """Real native success is consumed from the epoch and fails the final live gate."""
    Current = ObserveCurrentState(monkeypatch)
    OriginalSnapshot = Preparation.BuildTypedRouteCurrentCallerSnapshot
    OriginalMaterialize = Flow.PortalOperations._MaterializeCandidate
    Injected = []
    Visited = []
    States = []
    PublicationFailures = []
    OriginalPublication = Materialization.ValidateTypedRouteMaterializationPublication

    def Publication(State, Services):
        try:
            return OriginalPublication(State, Services)
        except RoutingStageError as Error:
            PublicationFailures.append(Error.Failure)
            raise

    def Snapshot(State, Context, Descriptors):
        if RejectPhysically and not Injected:
            Signal = Descriptors[0].Signal
            Position = State.Profiles[Signal].SourceAccessPath[-1]
            State.ForeignSelectedPinAccessClaimsBySignal[Signal] = (
                *State.ForeignSelectedPinAccessClaimsBySignal[Signal],
                ("epoch-test-owner", State.Resources.ResourceGraph.BuildRouteClaims({Position})),
            )
            Injected.append(Signal)
        return OriginalSnapshot(State, Context, Descriptors)

    def Materialize(*Arguments, **Keywords):
        State, _Services = Current[-1]
        Signal, Profile = Arguments[:2]
        if not Visited:
            assert Profile is not State.Profiles[Signal]
            assert Arguments[11].ResourceGraph is not State.Resources.ResourceGraph
            # The immutable epoch must retain the pre-mutation physical inputs.
            BeforePath = Profile.SourceAccessPath
            BeforeBlocks = Arguments[11].ResourceGraph.ActualBlocks
            if Mutation == "policy":
                State.Policy = replace(State.Policy, DetailedRouting=replace(
                    State.Policy.DetailedRouting,
                    LengthPenalty=State.Policy.DetailedRouting.LengthPenalty + 1,
                ))
            elif Mutation == "profile":
                State.Profiles[Signal] = replace(State.Profiles[Signal],
                    SourceAccessPath=(*State.Profiles[Signal].SourceAccessPath, (900, 1, 900)))
            elif Mutation == "resource":
                State.Resources.ResourceGraph.ActualBlocks |= {(900, 1, 900)}
            elif Mutation == "selected-claims":
                State.ForeignSelectedPinAccessClaimsBySignal["epoch-test"] = ()
            elif Mutation == "sibling-claims":
                State.AssemblySpecificSiblingAperturesBySignal["epoch-test"] = ()
            elif Mutation == "layer-count":
                State.LayerCount += 1
            elif Mutation == "portal-mode":
                State.UnreservedPortalMode = not State.UnreservedPortalMode
            elif Mutation == "request":
                State.RouteRequestsBySignal[Signal][0][3].append((900, 1, 900))
            elif Mutation == "metadata":
                Metadata = list(State.RouteMetadataBySignal[Signal][0])
                Metadata[-1] += 1
                State.RouteMetadataBySignal[Signal][0] = tuple(Metadata)
            else:
                State.CoarsePlan.Guides["epoch-test"] = frozenset({(900, 900)})
            assert Profile.SourceAccessPath == BeforePath
            assert Arguments[11].ResourceGraph.ActualBlocks == BeforeBlocks
            Visited.append(Signal)
            States.append(State)
        return OriginalMaterialize(*Arguments, **Keywords)

    monkeypatch.setattr(Preparation, "BuildTypedRouteCurrentCallerSnapshot", Snapshot)
    monkeypatch.setattr(Flow.PortalOperations, "_MaterializeCandidate", Materialize)
    monkeypatch.setattr(Materialization, "ValidateTypedRouteMaterializationPublication", Publication)
    with pytest.raises(RoutingStageError) as Error:
        PlaceAndRoutePcb(BuildSingleNandNetlist(), Strategy="routing-aware-placement-access")
    assert Visited
    # Placement orchestration may wrap the routing failure with its own stage.
    # Observe the actual publication boundary to prove stale authority is why
    # the operation fails, rather than accepting any top-level routing failure.
    assert PublicationFailures
    assert all(Failure.Stage == "TypedRouteMaterializationPublication"
               and Failure.Diagnostics["Complete"] is False
               and Failure.Diagnostics["Action"] == "reject-stale-typed-route-preparation"
               for Failure in PublicationFailures)
    assert any(Record.Admitted is not RejectPhysically
               for State in States
               for Record, _Produced in State.TypedNativePendingPhysicalAdmissions.values()
               if Record.PhysicalEvidence.Signal == Visited[0])
    assert all(not any(Record.PhysicalEvidence is not None
                      for Record in State.TypedNativeRouteAdmissionRecords)
               for State in States)
    assert all(not any(Batch["Counters"]["PhysicallyAccepted"] or Batch["Counters"]["PhysicallyRejected"]
                      for Batch in State.TypedNativeRouteBatches)
               for State in States)


@pytest.mark.parametrize("Mutation", ("metadata", "signal-association", "source-access-path"))
def test_changed_request_origin_before_dispatch_cannot_publish_complete_work(monkeypatch, Mutation):
    """Current signal/access provenance must agree before native work is submitted."""
    Current = ObserveCurrentState(monkeypatch)
    OriginalScheduler = Flow.ShouldUseMatureStagedInitialCandidateScheduler
    OriginalExecute = Preparation.ExecuteNativeRouteBatchOutcomesV1
    Modified = []
    Executions = []
    States = []

    def Scheduler(*Arguments, **Keywords):
        State, _Services = Current[-1]
        if not Modified:
            Rows = [(Signal, Requests) for Signal, Requests in State.RouteRequestsBySignal.items() if Requests]
            if Mutation == "metadata":
                Signal, _Requests = Rows[0]
                Metadata = list(State.RouteMetadataBySignal[Signal][0])
                Metadata[-1] += 10000
                State.RouteMetadataBySignal[Signal][0] = tuple(Metadata)
            elif Mutation == "source-access-path":
                Signal, _Requests = Rows[0]
                Profile = State.Profiles[Signal]
                Path = tuple(Profile.SourceAccessPath)
                Added = (
                    max(Position[0] for Position in (*Path, Profile.Root)) + 1,
                    Profile.Root[1], Profile.Root[2],
                )
                State.Profiles[Signal] = replace(
                    Profile, SourceAccessPath=(*Path, Added)
                )
            else:
                Signal, Requests = Rows[0]
                Moved = Requests.pop(0)
                Metadata = State.RouteMetadataBySignal[Signal].pop(0)
                State.RouteRequestsBySignal["different-current-signal"] = [Moved]
                State.RouteMetadataBySignal["different-current-signal"] = [Metadata]
            Modified.append(Mutation)
            States.append(State)
        return OriginalScheduler(*Arguments, **Keywords)

    def Execute(*Arguments, **Keywords):
        Executions.append(True)
        return OriginalExecute(*Arguments, **Keywords)

    monkeypatch.setattr(Flow, "ShouldUseMatureStagedInitialCandidateScheduler", Scheduler)
    monkeypatch.setattr(Preparation, "ExecuteNativeRouteBatchOutcomesV1", Execute)
    with pytest.raises(RoutingStageError):
        PlaceAndRoutePcb(BuildSingleNandNetlist(), Strategy="routing-aware-placement-access")
    assert Modified == [Mutation]
    assert not Executions
    assert all(not any(Record.NativeKind in {"Routed", "CompleteScopedNoPath"}
                       for Record in State.TypedNativeRouteOriginRecords)
               for State in States)
    assert all(not any(Record.Admitted is True for Record in State.TypedNativeRouteAdmissionRecords)
               for State in States)


def test_native_conversion_uses_owned_request_values_during_transient_source_change(monkeypatch):
    """A source-list mutation cannot make execution diverge from its sealed plan."""
    Current = ObserveCurrentState(monkeypatch)
    OriginalBuilder = Preparation.BuildTypedNativeCoarseRequest
    Captures = []

    def Build(*Arguments, **Keywords):
        State, _Services = Current[-1]
        SealedRequest = Arguments[4]
        assert type(SealedRequest) is tuple
        assert all(type(Value) is not list for Value in SealedRequest)
        Source = next(Request for Values in State.RouteRequestsBySignal.values()
                      for Request in Values
                      if Preparation.SealTypedRouteLegacyRequests((Request,))[0] == SealedRequest)
        Columns = Source[2]
        if Columns is not None:
            OriginalColumns = tuple(Columns)
            Columns.clear()
            try:
                assert SealedRequest[2] == OriginalColumns
                Result = OriginalBuilder(*Arguments, **Keywords)
            finally:
                Columns.extend(OriginalColumns)
            Captures.append(Result)
            return Result
        return OriginalBuilder(*Arguments, **Keywords)

    monkeypatch.setattr(Preparation, "BuildTypedNativeCoarseRequest", Build)
    Result = PlaceAndRoutePcb(BuildSingleNandNetlist(), Strategy="routing-aware-placement-access")
    assert Captures
    assert Result.Routed.ZeroResourceConflicts is True
