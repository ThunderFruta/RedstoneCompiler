use crate::Core::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Core::Models::{
    FactorizedRouteTreeAccessPayload, FactorizedRouteTreeGuidePayload, FactorizedRouteTreeRequest,
    FactorizedRouteTreeSelectionResult, Position, RouteTreeBatchResult, RoutingContext,
};
use crate::Core::Runtime::RoutingThreadPool;
use crate::Path::PathRouting::ManhattanDistance;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::{BTreeMap, HashMap, HashSet};

use super::SelectedWorldClaims::*;

/// Expands interned request factors inside Rust and performs the same finite
/// detailed route-tree batch as `GenerateRouteTreesNative`.  Repeated portal
/// payloads and guide-column expansion cross the Python boundary once rather
/// than once per Cartesian request value.
pub(crate) fn GenerateRouteTreesFactorizedNative(
    Context: &RoutingContext,
    AccessPayloads: Vec<FactorizedRouteTreeAccessPayload>,
    GuidePayloads: Vec<FactorizedRouteTreeGuidePayload>,
    Requests: Vec<FactorizedRouteTreeRequest>,
    MaximumRuntimeMilliseconds: u64,
) -> PyResult<RouteTreeBatchResult> {
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let TotalWork = Requests.len();
    for (AccessIndex, GuideIndex, ..) in &Requests {
        if *AccessIndex >= AccessPayloads.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "factorized route-tree request has an invalid access payload index",
            ));
        }
        if *GuideIndex >= GuidePayloads.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "factorized route-tree request has an invalid guide payload index",
            ));
        }
    }
    let PreparedAccessPayloads: Vec<_> = AccessPayloads
        .into_iter()
        .map(
            |(
                Starts,
                SourceBranch,
                TargetBranches,
                FrozenTargetBranches,
                RequiredNodes,
                BlockedNodes,
                MandatoryWire,
                MandatorySupport,
                MandatoryAir,
                MandatoryElectrical,
            )| PreparedFactorizedRouteTreeAccess {
                Starts,
                SourceBranch,
                TargetBranches,
                FrozenTargetBranches,
                RequiredNodes: RequiredNodes.into_iter().collect(),
                BlockedNodes: BlockedNodes.into_iter().collect(),
                MandatoryWire: MandatoryWire.into_iter().collect(),
                MandatorySupport: MandatorySupport.into_iter().collect(),
                MandatoryAir: MandatoryAir.into_iter().collect(),
                MandatoryElectrical: MandatoryElectrical.into_iter().collect(),
            },
        )
        .collect();
    let mut AllowedNodeValuesByGuide = Vec::with_capacity(GuidePayloads.len());
    for (
        GuideIndex,
        (AllowedColumns, _PreferredColumns, _ExactHintNodes, _CertifiedPaths, _CertifiedRepeaters),
    ) in GuidePayloads.iter().enumerate()
    {
        let mut AllowedNodes = Vec::new();
        for (ColumnIndex, Column) in AllowedColumns.iter().enumerate() {
            if (GuideIndex + ColumnIndex) % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return Ok(RouteTreeBatchResult {
                    RouteTrees: vec![None; TotalWork],
                    RepeaterReservations: vec![Vec::new(); TotalWork],
                    CompletionMask: vec![false; TotalWork],
                    DeadlineExceeded: true,
                    CompletedWork: 0,
                    TotalWork,
                });
            }
            if let Some(Values) = Context.NodesByColumn.get(Column) {
                AllowedNodes.extend(Values.iter().copied());
            }
        }
        AllowedNodes.sort_unstable();
        AllowedNodes.dedup();
        if std::env::var_os("RCS_DEBUG_SELECTED_WORLD_FULL_GRAPH").is_some() {
            AllowedNodes = Context.Adjacency.keys().copied().collect();
            AllowedNodes.sort_unstable();
        }
        AllowedNodeValuesByGuide.push(AllowedNodes);
    }
    let mut PreparedGuideIndexByKey = HashMap::new();
    let mut PreparedGuides = Vec::new();
    for (_AccessIndex, GuideIndex, _PreferredRoutingY, GuidePenalty, ..) in &Requests {
        let Key = (*GuideIndex, *GuidePenalty);
        if PreparedGuideIndexByKey.contains_key(&Key) {
            continue;
        }
        let Some(mut PreparedGuide) = Context.PrepareDetailedRouteGuide(
            &AllowedNodeValuesByGuide[*GuideIndex],
            &GuidePayloads[*GuideIndex].1,
            &[],
            *GuidePenalty,
            &Deadline,
        ) else {
            return Ok(RouteTreeBatchResult {
                RouteTrees: vec![None; TotalWork],
                RepeaterReservations: vec![Vec::new(); TotalWork],
                CompletionMask: vec![false; TotalWork],
                DeadlineExceeded: true,
                CompletedWork: 0,
                TotalWork,
            });
        };
        PreparedGuide.ExactHintNodes.extend(
            GuidePayloads[*GuideIndex]
                .2
                .iter()
                .copied()
                .filter(|Value| Context.Adjacency.contains_key(Value)),
        );
        PreparedGuide.CertifiedPaths = GuidePayloads[*GuideIndex].3.clone();
        PreparedGuide.CertifiedRepeaters = GuidePayloads[*GuideIndex].4.clone();
        PreparedGuideIndexByKey.insert(Key, PreparedGuides.len());
        PreparedGuides.push(PreparedGuide);
    }
    let PreparedRequests: Vec<_> = Requests
        .into_iter()
        .map(|Request| {
            let PreparedGuideIndex = PreparedGuideIndexByKey[&(Request.1, Request.3)];
            (PreparedGuideIndex, Request)
        })
        .collect();
    let WorkResults: Vec<(Option<Vec<Position>>, Vec<(Position, String)>, bool)> =
        RoutingThreadPool().install(|| {
            PreparedRequests
                .into_par_iter()
                .map(
                    |(
                        PreparedGuideIndex,
                        (
                            AccessIndex,
                            _GuideIndex,
                            PreferredRoutingY,
                            _GuidePenalty,
                            BendPenalty,
                            ViaPenalty,
                            MaximumExpansionCount,
                        ),
                    )| {
                        if Deadline.Check() {
                            return (None, Vec::new(), false);
                        }
                        let Access = &PreparedAccessPayloads[AccessIndex];
                        let SearchResult = Context
                            .GenerateRouteTreeClaimAwarePreparedDetailedNative(
                                &Access.Starts,
                                &Access.TargetBranches,
                                &Access.FrozenTargetBranches,
                                &PreparedGuides[PreparedGuideIndex],
                                &Access.RequiredNodes,
                                &Access.BlockedNodes,
                                PreferredRoutingY,
                                BendPenalty,
                                ViaPenalty,
                                true,
                                Some(&Access.SourceBranch),
                                MaximumExpansionCount,
                                &Deadline,
                                &Access.MandatoryWire,
                                &Access.MandatorySupport,
                                &Access.MandatoryAir,
                                "",
                            );
                        let Completed = !SearchResult.IsBudgetExpired;
                        let RouteTree = SearchResult.IsRouted.then_some(SearchResult.Nodes);
                        (RouteTree, SearchResult.RepeaterReservations, Completed)
                    },
                )
                .collect()
        });
    let CompletionMask: Vec<_> = WorkResults
        .iter()
        .map(|(_RouteTree, _Repeaters, Completed)| *Completed)
        .collect();
    let CompletedWork = CompletionMask.iter().filter(|Value| **Value).count();
    let (RouteTrees, RepeaterReservations): (Vec<_>, Vec<_>) = WorkResults
        .into_iter()
        .map(|(RouteTree, Repeaters, _Completed)| (RouteTree, Repeaters))
        .unzip();
    Ok(RouteTreeBatchResult {
        RouteTrees,
        RepeaterReservations,
        CompletionMask,
        DeadlineExceeded: Deadline.WasExceeded(),
        CompletedWork,
        TotalWork,
    })
}

/// Generate exact selected-world route candidates in deterministic waves and
/// solve their physical claim capacity inside the same bounded native call.
/// This avoids eagerly expanding every declared request shape before the
/// assignment solver can use its first witness.  A successful witness is
/// exact; exhausted work without a witness remains incomplete.
pub(crate) fn GenerateAndAssignRouteTreesFactorizedNative(
    Context: &RoutingContext,
    AccessPayloads: Vec<FactorizedRouteTreeAccessPayload>,
    GuidePayloads: Vec<FactorizedRouteTreeGuidePayload>,
    Requests: Vec<FactorizedRouteTreeRequest>,
    SignalRequestIndices: Vec<(String, Vec<usize>)>,
    MaximumAssignmentExpansionCount: usize,
    MaximumRuntimeMilliseconds: u64,
) -> PyResult<FactorizedRouteTreeSelectionResult> {
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let TotalWork = Requests.len();
    if SignalRequestIndices.is_empty()
        || SignalRequestIndices
            .iter()
            .any(|(Signal, Values)| Signal.is_empty() || Values.is_empty())
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "factorized selected-world assignment requires nonempty signal domains",
        ));
    }
    let mut SeenRequestIndices = HashSet::new();
    for (_Signal, Values) in &SignalRequestIndices {
        for RequestIndex in Values {
            if *RequestIndex >= Requests.len() || !SeenRequestIndices.insert(*RequestIndex) {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "factorized selected-world signal domains must uniquely own valid requests",
                ));
            }
        }
    }
    for (AccessIndex, GuideIndex, ..) in &Requests {
        if *AccessIndex >= AccessPayloads.len() || *GuideIndex >= GuidePayloads.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "factorized selected-world request references an invalid payload",
            ));
        }
    }
    let PreparedAccessPayloads: Vec<_> = AccessPayloads
        .into_iter()
        .map(
            |(
                Starts,
                SourceBranch,
                TargetBranches,
                FrozenTargetBranches,
                RequiredNodes,
                BlockedNodes,
                MandatoryWire,
                MandatorySupport,
                MandatoryAir,
                MandatoryElectrical,
            )| PreparedFactorizedRouteTreeAccess {
                Starts,
                SourceBranch,
                TargetBranches,
                FrozenTargetBranches,
                RequiredNodes: RequiredNodes.into_iter().collect(),
                BlockedNodes: BlockedNodes.into_iter().collect(),
                MandatoryWire: MandatoryWire.into_iter().collect(),
                MandatorySupport: MandatorySupport.into_iter().collect(),
                MandatoryAir: MandatoryAir.into_iter().collect(),
                MandatoryElectrical: MandatoryElectrical.into_iter().collect(),
            },
        )
        .collect();
    let GroupCount = SignalRequestIndices.len();
    let mut PreparedGuideIndexByKey = HashMap::new();
    let mut PreparedGuides = Vec::new();
    let mut CandidateGroups = vec![Vec::<ExactSelectedWorldRouteCandidate>::new(); GroupCount];
    let mut CompletionMask = vec![false; TotalWork];
    let mut NextRequestOffsetByGroup = vec![0usize; GroupCount];
    let mut RouteExpansionCountByRequest = vec![0usize; TotalWork];
    let mut AlternativeBlockedNodesByRequest = vec![HashSet::<Position>::new(); TotalWork];
    let EffectiveMaximumAssignmentExpansionCount =
        MaximumAssignmentExpansionCount.clamp(1, 1_000_000);
    let mut AssignmentExpansionCount = 0usize;
    let mut GeneratedRequestCount = 0usize;
    let mut SawIncompleteRequest = false;
    let mut ReevaluateCandidateAssignment = false;
    let mut GreedyRepairAttemptKeys = HashSet::<(usize, usize, Vec<(usize, usize)>)>::new();
    let mut GreedyBlockedNodesByForeignCandidate =
        HashMap::<(usize, usize, usize), HashSet<Position>>::new();
    loop {
        let HasEmptyCandidateGroup = CandidateGroups.iter().any(Vec::is_empty);
        let Wave = if ReevaluateCandidateAssignment {
            Vec::new()
        } else {
            SignalRequestIndices
                .iter()
                .enumerate()
                .filter(|(GroupIndex, _Value)| {
                    !HasEmptyCandidateGroup || CandidateGroups[*GroupIndex].is_empty()
                })
                .filter_map(|(GroupIndex, (_Signal, RequestIndices))| {
                    RequestIndices
                        .get(NextRequestOffsetByGroup[GroupIndex])
                        .copied()
                        .map(|RequestIndex| (GroupIndex, RequestIndex))
                })
                .collect::<Vec<_>>()
        };
        if Deadline.Check() || (Wave.is_empty() && !ReevaluateCandidateAssignment) {
            let CompletedWork = CompletionMask.iter().filter(|Value| **Value).count();
            let DeadlineExceeded = Deadline.Check();
            return Ok(FactorizedRouteTreeSelectionResult {
                RouteTrees: vec![None; TotalWork],
                RepeaterReservations: vec![Vec::new(); TotalWork],
                CompletionMask,
                SelectedRequestIndices: Vec::new(),
                Success: false,
                Complete: !DeadlineExceeded && !SawIncompleteRequest && Wave.is_empty(),
                DeadlineExceeded,
                WorkCapExceeded: SawIncompleteRequest,
                AssignmentExpansionCount,
                GeneratedRequestCount,
                GeneratedRequestCountsBySignal: SignalRequestIndices
                    .iter()
                    .enumerate()
                    .map(|(GroupIndex, (Signal, _Requests))| {
                        (Signal.clone(), NextRequestOffsetByGroup[GroupIndex])
                    })
                    .collect(),
                CandidateCountsBySignal: SignalRequestIndices
                    .iter()
                    .enumerate()
                    .map(|(GroupIndex, (Signal, _Requests))| {
                        (Signal.clone(), CandidateGroups[GroupIndex].len())
                    })
                    .collect(),
                CompletedWork,
                TotalWork,
            });
        }
        ReevaluateCandidateAssignment = false;
        for (_GroupIndex, RequestIndex) in &Wave {
            let Request = Requests[*RequestIndex];
            let Key = (Request.1, Request.2, Request.3);
            if PreparedGuideIndexByKey.contains_key(&Key) {
                continue;
            }
            let PreparedGuide = if std::env::var_os("RCS_DEBUG_SELECTED_WORLD_FULL_GRAPH").is_some()
            {
                let AllNodes = Context.Adjacency.keys().copied().collect::<Vec<_>>();
                let VerticalCosts = AllNodes
                    .iter()
                    .filter_map(|PositionValue| {
                        let Distance = (PositionValue.1 - Request.2).abs();
                        (Distance > 0)
                            .then_some((*PositionValue, Distance.saturating_mul(Request.3.max(1))))
                    })
                    .collect::<Vec<_>>();
                Context.PrepareDetailedRouteGuide(
                    &AllNodes,
                    &GuidePayloads[Request.1].1,
                    &VerticalCosts,
                    Request.3,
                    &Deadline,
                )
            } else {
                Context.PrepareDetailedRouteGuideFromColumns(
                    &GuidePayloads[Request.1].0,
                    &GuidePayloads[Request.1].1,
                    Request.2,
                    Request.3,
                    &Deadline,
                )
            };
            let Some(mut PreparedGuide) = PreparedGuide else {
                continue;
            };
            PreparedGuide.ExactHintNodes.extend(
                GuidePayloads[Request.1]
                    .2
                    .iter()
                    .copied()
                    .filter(|Value| Context.Adjacency.contains_key(Value)),
            );
            PreparedGuide.CertifiedPaths = GuidePayloads[Request.1].3.clone();
            PreparedGuide.CertifiedRepeaters = GuidePayloads[Request.1].4.clone();
            PreparedGuideIndexByKey.insert(Key, PreparedGuides.len());
            PreparedGuides.push(PreparedGuide);
        }
        if Deadline.Check() {
            continue;
        }
        for (GroupIndex, _RequestIndex) in &Wave {
            NextRequestOffsetByGroup[*GroupIndex] += 1;
        }
        let WaveResults = RoutingThreadPool().install(|| {
            Wave.par_iter()
                .map(|(GroupIndex, RequestIndex)| {
                    if Deadline.Check() {
                        return (*GroupIndex, *RequestIndex, None, false, 0usize);
                    }
                    let (
                        AccessIndex,
                        GuideIndex,
                        PreferredRoutingY,
                        GuidePenalty,
                        BendPenalty,
                        ViaPenalty,
                        MaximumExpansionCount,
                    ) = Requests[*RequestIndex];
                    let Some(PreparedGuideIndex) = PreparedGuideIndexByKey
                        .get(&(GuideIndex, PreferredRoutingY, GuidePenalty))
                        .copied()
                    else {
                        return (*GroupIndex, *RequestIndex, None, false, 0usize);
                    };
                    let Access = &PreparedAccessPayloads[AccessIndex];
                    let SearchResult = Context.GenerateRouteTreeClaimAwarePreparedDetailedNative(
                        &Access.Starts,
                        &Access.TargetBranches,
                        &Access.FrozenTargetBranches,
                        &PreparedGuides[PreparedGuideIndex],
                        &Access.RequiredNodes,
                        &Access.BlockedNodes,
                        PreferredRoutingY,
                        BendPenalty,
                        ViaPenalty,
                        true,
                        Some(&Access.SourceBranch),
                        MaximumExpansionCount,
                        &Deadline,
                        &Access.MandatoryWire,
                        &Access.MandatorySupport,
                        &Access.MandatoryAir,
                        &SignalRequestIndices[*GroupIndex].0,
                    );
                    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some()
                        || std::env::var_os("RCS_DEBUG_SELECTED_WORLD_SUMMARY").is_some()
                    {
                        eprintln!(
                            "selected detailed signal={} status={} reason={} expansions={} rejected={} nogoods={}",
                            SignalRequestIndices[*GroupIndex].0,
                            SearchResult.Status,
                            SearchResult.NoPathReason,
                            SearchResult.ExpansionCount,
                            SearchResult.RejectedPathCount,
                            SearchResult.NoGoodCount,
                        );
                    }
                    let Complete = !SearchResult.IsBudgetExpired;
                    let Candidate = SearchResult.IsRouted.then(|| {
                        let Claims = BuildExactSelectedWorldRouteClaims(
                            Context,
                            &SearchResult.Nodes,
                            Access,
                        );
                        ExactSelectedWorldRouteCandidate {
                            RequestIndex: *RequestIndex,
                            Nodes: SearchResult.Nodes,
                            RepeaterReservations: SearchResult.RepeaterReservations,
                            Claims,
                        }
                    });
                    (
                        *GroupIndex,
                        *RequestIndex,
                        Candidate,
                        Complete,
                        SearchResult.ExpansionCount,
                    )
                })
                .collect::<Vec<_>>()
        });
        for (GroupIndex, RequestIndex, Candidate, Complete, RouteExpansionCount) in WaveResults {
            GeneratedRequestCount += 1;
            RouteExpansionCountByRequest[RequestIndex] =
                RouteExpansionCountByRequest[RequestIndex].saturating_add(RouteExpansionCount);
            CompletionMask[RequestIndex] = Complete;
            SawIncompleteRequest |= !Complete;
            if let Some(Value) = Candidate {
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE_VERBOSE").is_some() {
                    eprintln!(
                        "selected detailed candidate signal={} nodes={:?} repeaters={:?}",
                        SignalRequestIndices[GroupIndex].0, Value.Nodes, Value.RepeaterReservations,
                    );
                }
                CandidateGroups[GroupIndex].push(Value);
            }
        }
        if Deadline.Check() || CandidateGroups.iter().any(Vec::is_empty) {
            continue;
        }
        if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
            for FirstIndex in 0..CandidateGroups.len() {
                for SecondIndex in FirstIndex + 1..CandidateGroups.len() {
                    if ExactSelectedWorldClaimsConflict(
                        &CandidateGroups[FirstIndex][0].Claims,
                        &CandidateGroups[SecondIndex][0].Claims,
                    ) {
                        let FirstAccess = &PreparedAccessPayloads
                            [Requests[CandidateGroups[FirstIndex][0].RequestIndex].0];
                        let SecondAccess = &PreparedAccessPayloads
                            [Requests[CandidateGroups[SecondIndex][0].RequestIndex].0];
                        eprintln!(
                            "selected detailed cross-conflict first={} second={} first_nodes={:?} second_nodes={:?}",
                            SignalRequestIndices[FirstIndex].0,
                            SignalRequestIndices[SecondIndex].0,
                            FindExactSelectedWorldMovableConflictNodes(
                                Context,
                                &CandidateGroups[FirstIndex][0],
                                FirstAccess,
                                &CandidateGroups[SecondIndex][0].Claims,
                            ),
                            FindExactSelectedWorldMovableConflictNodes(
                                Context,
                                &CandidateGroups[SecondIndex][0],
                                SecondAccess,
                                &CandidateGroups[FirstIndex][0].Claims,
                            ),
                        );
                    }
                }
            }
        }
        let mut SelectedCandidateIndices = Vec::with_capacity(GroupCount);
        if let Some(SelectedCandidateIndices) = SearchExactSelectedWorldAssignment(
            &CandidateGroups,
            0,
            &mut SelectedCandidateIndices,
            &mut AssignmentExpansionCount,
            EffectiveMaximumAssignmentExpansionCount,
            &Deadline,
        ) {
            let mut RouteTrees = vec![None; TotalWork];
            let mut RepeaterReservations = vec![Vec::new(); TotalWork];
            let mut SelectedRequestIndices = Vec::with_capacity(GroupCount);
            for (GroupIndex, CandidateIndex) in SelectedCandidateIndices.into_iter().enumerate() {
                let Candidate = &CandidateGroups[GroupIndex][CandidateIndex];
                RouteTrees[Candidate.RequestIndex] = Some(Candidate.Nodes.clone());
                RepeaterReservations[Candidate.RequestIndex] =
                    Candidate.RepeaterReservations.clone();
                SelectedRequestIndices.push(Candidate.RequestIndex);
            }
            let CompletedWork = CompletionMask.iter().filter(|Value| **Value).count();
            return Ok(FactorizedRouteTreeSelectionResult {
                RouteTrees,
                RepeaterReservations,
                CompletionMask,
                SelectedRequestIndices,
                Success: true,
                Complete: true,
                DeadlineExceeded: false,
                WorkCapExceeded: false,
                AssignmentExpansionCount,
                GeneratedRequestCount,
                GeneratedRequestCountsBySignal: SignalRequestIndices
                    .iter()
                    .enumerate()
                    .map(|(GroupIndex, (Signal, _Requests))| {
                        (Signal.clone(), NextRequestOffsetByGroup[GroupIndex])
                    })
                    .collect(),
                CandidateCountsBySignal: SignalRequestIndices
                    .iter()
                    .enumerate()
                    .map(|(GroupIndex, (Signal, _Requests))| {
                        (Signal.clone(), CandidateGroups[GroupIndex].len())
                    })
                    .collect(),
                CompletedWork,
                TotalWork,
            });
        }
        // The independently generated least-cost trees can form a chain of
        // obsolete pairwise conflicts: repairing a late signal against the
        // first candidate of its sibling does not help after that sibling has
        // already moved.  Before adding another pairwise no-good, build one
        // deterministic partial witness in signal order.  When a group has no
        // compatible existing value, materialize one alternative against the
        // exact claims already selected for every earlier group.  This stays
        // inside the same native invocation and consumes the request's
        // original expansion share and absolute deadline.
        let mut GreedyGroupOrder = (0..CandidateGroups.len()).collect::<Vec<_>>();
        GreedyGroupOrder.sort_by_key(|GroupIndex| {
            let Candidate = &CandidateGroups[*GroupIndex][0];
            let Access = &PreparedAccessPayloads[Requests[Candidate.RequestIndex].0];
            let mut ConflictDegree = 0usize;
            let mut ImmovableConflictDegree = 0usize;
            let mut MovableConflictNodeCount = 0usize;
            for (OtherGroupIndex, OtherGroup) in CandidateGroups.iter().enumerate() {
                if OtherGroupIndex == *GroupIndex
                    || !ExactSelectedWorldClaimsConflict(&Candidate.Claims, &OtherGroup[0].Claims)
                {
                    continue;
                }
                ConflictDegree += 1;
                let MovableConflictNodes = FindExactSelectedWorldMovableConflictNodes(
                    Context,
                    Candidate,
                    Access,
                    &OtherGroup[0].Claims,
                );
                if MovableConflictNodes.is_empty() {
                    ImmovableConflictDegree += 1;
                }
                MovableConflictNodeCount =
                    MovableConflictNodeCount.saturating_add(MovableConflictNodes.len());
            }
            (
                CandidateGroups[*GroupIndex].len(),
                std::cmp::Reverse(ImmovableConflictDegree),
                std::cmp::Reverse(ConflictDegree),
                MovableConflictNodeCount,
                *GroupIndex,
            )
        });
        if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
            eprintln!(
                "selected detailed greedy order={:?}",
                GreedyGroupOrder
                    .iter()
                    .map(|GroupIndex| SignalRequestIndices[*GroupIndex].0.clone())
                    .collect::<Vec<_>>()
            );
        }
        let mut GreedySelectedCandidateIndices = BTreeMap::<usize, usize>::new();
        let mut AddedGreedyAlternative = false;
        let mut AdvancedGreedyNoGood = false;
        for GroupIndex in GreedyGroupOrder {
            let CompatibleCandidateIndex = CandidateGroups[GroupIndex]
                .iter()
                .enumerate()
                .filter(|(_CandidateIndex, Candidate)| {
                    GreedySelectedCandidateIndices.iter().all(
                        |(PriorGroupIndex, PriorCandidateIndex)| {
                            !ExactSelectedWorldClaimsConflict(
                                &Candidate.Claims,
                                &CandidateGroups[*PriorGroupIndex][*PriorCandidateIndex].Claims,
                            )
                        },
                    )
                })
                .min_by_key(|(CandidateIndex, Candidate)| {
                    let mut FutureDeadEndCount = 0usize;
                    let mut FutureConflictCount = 0usize;
                    for (FutureGroupIndex, FutureGroup) in CandidateGroups.iter().enumerate() {
                        if FutureGroupIndex == GroupIndex
                            || GreedySelectedCandidateIndices.contains_key(&FutureGroupIndex)
                        {
                            continue;
                        }
                        let CompatibleFutureCount = FutureGroup
                            .iter()
                            .filter(|FutureCandidate| {
                                !ExactSelectedWorldClaimsConflict(
                                    &Candidate.Claims,
                                    &FutureCandidate.Claims,
                                )
                            })
                            .count();
                        FutureDeadEndCount += usize::from(CompatibleFutureCount == 0);
                        FutureConflictCount = FutureConflictCount
                            .saturating_add(FutureGroup.len() - CompatibleFutureCount);
                    }
                    (FutureDeadEndCount, FutureConflictCount, *CandidateIndex)
                })
                .map(|(CandidateIndex, _Candidate)| CandidateIndex);
            if let Some(CandidateIndex) = CompatibleCandidateIndex {
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                    eprintln!(
                        "selected detailed greedy choose signal={} candidate={}",
                        SignalRequestIndices[GroupIndex].0, CandidateIndex,
                    );
                }
                GreedySelectedCandidateIndices.insert(GroupIndex, CandidateIndex);
                continue;
            }
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                for (CandidateIndex, Candidate) in CandidateGroups[GroupIndex].iter().enumerate() {
                    let Access = &PreparedAccessPayloads[Requests[Candidate.RequestIndex].0];
                    for (PriorGroupIndex, PriorCandidateIndex) in &GreedySelectedCandidateIndices {
                        let PriorCandidate =
                            &CandidateGroups[*PriorGroupIndex][*PriorCandidateIndex];
                        if ExactSelectedWorldClaimsConflict(
                            &Candidate.Claims,
                            &PriorCandidate.Claims,
                        ) {
                            eprintln!(
                                "selected detailed greedy conflict signal={} candidate={} prior={} prior_candidate={} movable={:?} resources={:?}",
                                SignalRequestIndices[GroupIndex].0,
                                CandidateIndex,
                                SignalRequestIndices[*PriorGroupIndex].0,
                                PriorCandidateIndex,
                                FindExactSelectedWorldMovableConflictNodes(
                                    Context,
                                    Candidate,
                                    Access,
                                    &PriorCandidate.Claims,
                                ),
                                ExactSelectedWorldConflictResources(
                                    &Candidate.Claims,
                                    &PriorCandidate.Claims,
                                ),
                            );
                        }
                    }
                }
            }
            let mut RepairOptions = CandidateGroups[GroupIndex]
                .iter()
                .enumerate()
                .filter_map(|(CandidateIndex, Candidate)| {
                    let Access = &PreparedAccessPayloads[Requests[Candidate.RequestIndex].0];
                    let mut ConflictNodes = HashSet::new();
                    let mut HasNewPairConflictNode = false;
                    for (PriorGroupIndex, PriorCandidateIndex) in &GreedySelectedCandidateIndices {
                        let PairConflictNodes = FindExactSelectedWorldMovableConflictNodes(
                            Context,
                            Candidate,
                            Access,
                            &CandidateGroups[*PriorGroupIndex][*PriorCandidateIndex].Claims,
                        );
                        let RetainedPairNoGood = GreedyBlockedNodesByForeignCandidate.get(&(
                            Candidate.RequestIndex,
                            *PriorGroupIndex,
                            *PriorCandidateIndex,
                        ));
                        HasNewPairConflictNode |= PairConflictNodes.iter().any(|PositionValue| {
                            RetainedPairNoGood.is_none_or(|Values| !Values.contains(PositionValue))
                        });
                        ConflictNodes.extend(PairConflictNodes);
                    }
                    if ConflictNodes.is_empty() || !HasNewPairConflictNode {
                        return None;
                    }
                    let mut NewConflictNodes = ConflictNodes.into_iter().collect::<Vec<_>>();
                    NewConflictNodes.sort_unstable();
                    if NewConflictNodes.is_empty() {
                        return None;
                    }
                    let RepairAttemptKey = (
                        GroupIndex,
                        CandidateIndex,
                        GreedySelectedCandidateIndices
                            .iter()
                            .map(|(PriorGroupIndex, PriorCandidateIndex)| {
                                (*PriorGroupIndex, *PriorCandidateIndex)
                            })
                            .collect::<Vec<_>>(),
                    );
                    (!GreedyRepairAttemptKeys.contains(&RepairAttemptKey)).then_some((
                        NewConflictNodes.len(),
                        GroupIndex,
                        CandidateIndex,
                        Candidate.clone(),
                        NewConflictNodes,
                        RepairAttemptKey,
                        None,
                    ))
                })
                .collect::<Vec<_>>();
            for (CurrentCandidateIndex, CurrentCandidate) in
                CandidateGroups[GroupIndex].iter().enumerate()
            {
                for (PriorGroupIndex, PriorCandidateIndex) in &GreedySelectedCandidateIndices {
                    let PriorCandidate = &CandidateGroups[*PriorGroupIndex][*PriorCandidateIndex];
                    if !ExactSelectedWorldClaimsConflict(
                        &CurrentCandidate.Claims,
                        &PriorCandidate.Claims,
                    ) {
                        continue;
                    }
                    let PriorAccess =
                        &PreparedAccessPayloads[Requests[PriorCandidate.RequestIndex].0];
                    let PriorConflictNodes = FindExactSelectedWorldMovableConflictNodes(
                        Context,
                        PriorCandidate,
                        PriorAccess,
                        &CurrentCandidate.Claims,
                    );
                    if PriorConflictNodes.is_empty()
                        || PriorConflictNodes.iter().all(|PositionValue| {
                            GreedyBlockedNodesByForeignCandidate
                                .get(&(
                                    PriorCandidate.RequestIndex,
                                    GroupIndex,
                                    CurrentCandidateIndex,
                                ))
                                .is_some_and(|Values| Values.contains(PositionValue))
                        })
                    {
                        continue;
                    }
                    let mut NewConflictNodes = PriorConflictNodes.into_iter().collect::<Vec<_>>();
                    NewConflictNodes.sort_unstable();
                    if NewConflictNodes.is_empty() {
                        continue;
                    }
                    let mut ForeignCandidateIndices = GreedySelectedCandidateIndices
                        .iter()
                        .filter(|(SelectedGroupIndex, _CandidateIndex)| {
                            **SelectedGroupIndex != *PriorGroupIndex
                        })
                        .map(|(SelectedGroupIndex, CandidateIndex)| {
                            (*SelectedGroupIndex, *CandidateIndex)
                        })
                        .collect::<Vec<_>>();
                    ForeignCandidateIndices.push((GroupIndex, CurrentCandidateIndex));
                    ForeignCandidateIndices.sort_unstable();
                    let RepairAttemptKey = (
                        *PriorGroupIndex,
                        *PriorCandidateIndex,
                        ForeignCandidateIndices,
                    );
                    if GreedyRepairAttemptKeys.contains(&RepairAttemptKey) {
                        continue;
                    }
                    RepairOptions.push((
                        NewConflictNodes.len(),
                        *PriorGroupIndex,
                        *PriorCandidateIndex,
                        PriorCandidate.clone(),
                        NewConflictNodes,
                        RepairAttemptKey,
                        Some(CurrentCandidateIndex),
                    ));
                }
            }
            let RepairOption = RepairOptions.into_iter().min_by_key(
                |(
                    NodeCount,
                    RepairGroupIndex,
                    CandidateIndex,
                    Candidate,
                    Nodes,
                    _RepairAttemptKey,
                    FixedCurrentCandidateIndex,
                )| {
                    let RequestIndex = Candidate.RequestIndex;
                    let RemainingExpansionCount = Requests[RequestIndex]
                        .6
                        .saturating_sub(RouteExpansionCountByRequest[RequestIndex]);
                    (
                        // First try materializing the group that actually
                        // failed against the complete partial witness.  The
                        // reverse options remain available, but preferring
                        // them here reroutes an already accepted sibling once
                        // for every candidate of the failing group and can
                        // consume that sibling's bounded request share before
                        // the failing group is searched at all.
                        FixedCurrentCandidateIndex.is_some() as usize,
                        std::cmp::Reverse(RemainingExpansionCount),
                        *NodeCount,
                        *RepairGroupIndex,
                        *CandidateIndex,
                        Nodes.first().copied(),
                    )
                },
            );
            let Some((
                _NodeCount,
                RepairGroupIndex,
                _CandidateIndex,
                BaseCandidate,
                NewConflictNodes,
                RepairAttemptKey,
                FixedCurrentCandidateIndex,
            )) = RepairOption
            else {
                break;
            };
            let ForeignCandidateIndices = RepairAttemptKey.2.clone();
            GreedyRepairAttemptKeys.insert(RepairAttemptKey);
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                eprintln!(
                    "selected detailed greedy repair signal={} candidate={} blocked_nodes={}",
                    SignalRequestIndices[RepairGroupIndex].0,
                    _CandidateIndex,
                    NewConflictNodes.len(),
                );
            }
            let RequestIndex = BaseCandidate.RequestIndex;
            let (
                AccessIndex,
                GuideIndex,
                PreferredRoutingY,
                GuidePenalty,
                BendPenalty,
                ViaPenalty,
                MaximumExpansionCount,
            ) = Requests[RequestIndex];
            let Access = &PreparedAccessPayloads[AccessIndex];
            AdvancedGreedyNoGood = true;
            let RemainingExpansionCount =
                MaximumExpansionCount.saturating_sub(RouteExpansionCountByRequest[RequestIndex]);
            if RemainingExpansionCount == 0 {
                SawIncompleteRequest = true;
                break;
            }
            let Some(PreparedGuideIndex) = PreparedGuideIndexByKey
                .get(&(GuideIndex, PreferredRoutingY, GuidePenalty))
                .copied()
            else {
                SawIncompleteRequest = true;
                break;
            };
            let mut GreedyBlockedNodes = Access.BlockedNodes.clone();
            for (ForeignGroupIndex, ForeignCandidateIndex) in ForeignCandidateIndices {
                let ForeignCandidate = &CandidateGroups[ForeignGroupIndex][ForeignCandidateIndex];
                let PairConflictNodes = FindExactSelectedWorldMovableConflictNodes(
                    Context,
                    &BaseCandidate,
                    Access,
                    &ForeignCandidate.Claims,
                );
                let ExactForeignBlockedNodes = BuildExactSelectedWorldForeignBlockedNodes(
                    Context,
                    [ForeignCandidate.Claims.clone()],
                );
                let LocalExactForeignBlockedNodes = ExactForeignBlockedNodes
                    .into_iter()
                    .filter(|PositionValue| {
                        PairConflictNodes.iter().any(|ConflictNode| {
                            ManhattanDistance(*PositionValue, *ConflictNode) <= 2
                        })
                    })
                    .collect::<Vec<_>>();
                let RetainedPairNoGood = GreedyBlockedNodesByForeignCandidate
                    .entry((RequestIndex, ForeignGroupIndex, ForeignCandidateIndex))
                    .or_default();
                RetainedPairNoGood.extend(PairConflictNodes);
                RetainedPairNoGood.extend(LocalExactForeignBlockedNodes);
                GreedyBlockedNodes.extend(RetainedPairNoGood.iter().copied());
            }
            GreedyBlockedNodes.extend(NewConflictNodes);
            let SearchResult = Context.GenerateRouteTreeClaimAwarePreparedDetailedNative(
                &Access.Starts,
                &Access.TargetBranches,
                &Access.FrozenTargetBranches,
                &PreparedGuides[PreparedGuideIndex],
                &Access.RequiredNodes,
                &GreedyBlockedNodes,
                PreferredRoutingY,
                BendPenalty,
                ViaPenalty,
                true,
                Some(&Access.SourceBranch),
                RemainingExpansionCount,
                &Deadline,
                &Access.MandatoryWire,
                &Access.MandatorySupport,
                &Access.MandatoryAir,
                &SignalRequestIndices[RepairGroupIndex].0,
            );
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                eprintln!(
                    "selected detailed greedy result signal={} status={} reason={} expansions={} blocked_total={} foreign_sets={}",
                    SignalRequestIndices[RepairGroupIndex].0,
                    SearchResult.Status,
                    SearchResult.NoPathReason,
                    SearchResult.ExpansionCount,
                    GreedyBlockedNodes.len(),
                    usize::from(FixedCurrentCandidateIndex.is_some()),
                );
            }
            GeneratedRequestCount += 1;
            RouteExpansionCountByRequest[RequestIndex] = RouteExpansionCountByRequest[RequestIndex]
                .saturating_add(SearchResult.ExpansionCount);
            if SearchResult.IsBudgetExpired {
                SawIncompleteRequest = true;
                break;
            }
            if SearchResult.IsRouted {
                let Claims =
                    BuildExactSelectedWorldRouteClaims(Context, &SearchResult.Nodes, Access);
                let NewCandidate = ExactSelectedWorldRouteCandidate {
                    RequestIndex,
                    Nodes: SearchResult.Nodes,
                    RepeaterReservations: SearchResult.RepeaterReservations,
                    Claims,
                };
                if !CandidateGroups[RepairGroupIndex].iter().any(|Existing| {
                    Existing.Nodes == NewCandidate.Nodes
                        && Existing.RepeaterReservations == NewCandidate.RepeaterReservations
                }) {
                    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                        eprintln!(
                            "selected detailed greedy candidate signal={} nodes={:?} repeaters={:?}",
                            SignalRequestIndices[RepairGroupIndex].0,
                            NewCandidate.Nodes,
                            NewCandidate.RepeaterReservations,
                        );
                    }
                    CandidateGroups[RepairGroupIndex].push(NewCandidate);
                    AddedGreedyAlternative = true;
                }
            }
            break;
        }
        if AddedGreedyAlternative || (AdvancedGreedyNoGood && !SawIncompleteRequest) {
            ReevaluateCandidateAssignment = true;
            continue;
        }
        // A selected guide factor can admit multiple exact route trees.  If
        // the least-cost trees conflict across signals, add one exact
        // movable-node no-good and materialize the next tree inside this
        // same bounded native operation.  The request's original expansion
        // cap and the shared absolute deadline remain authoritative.
        let BlockingPair = (0..CandidateGroups.len()).find_map(|FirstIndex| {
            (FirstIndex + 1..CandidateGroups.len()).find_map(|SecondIndex| {
                CandidateGroups[FirstIndex]
                    .iter()
                    .all(|First| {
                        CandidateGroups[SecondIndex].iter().all(|Second| {
                            ExactSelectedWorldClaimsConflict(&First.Claims, &Second.Claims)
                        })
                    })
                    .then_some((FirstIndex, SecondIndex))
            })
        });
        let mut AddedConflictAlternative = false;
        if let Some((FirstIndex, SecondIndex)) = BlockingPair {
            let mut AlternativeOptions = Vec::<(usize, usize, usize, Vec<Position>)>::new();
            let AggregateInitialConflictNodes = |GroupIndex: usize,
                                                 Candidate: &ExactSelectedWorldRouteCandidate|
             -> Vec<Position> {
                let Access = &PreparedAccessPayloads[Requests[Candidate.RequestIndex].0];
                let mut Values = HashSet::new();
                for (OtherGroupIndex, OtherGroup) in CandidateGroups.iter().enumerate() {
                    if OtherGroupIndex == GroupIndex {
                        continue;
                    }
                    let Some(Other) = OtherGroup.first() else {
                        continue;
                    };
                    Values.extend(FindExactSelectedWorldMovableConflictNodes(
                        Context,
                        Candidate,
                        Access,
                        &Other.Claims,
                    ));
                }
                let mut Values = Values.into_iter().collect::<Vec<_>>();
                Values.sort_unstable();
                Values
            };
            for (CandidateIndex, Candidate) in CandidateGroups[FirstIndex].iter().enumerate() {
                let Nodes = AggregateInitialConflictNodes(FirstIndex, Candidate);
                if !Nodes.is_empty() {
                    AlternativeOptions.push((Nodes.len(), FirstIndex, CandidateIndex, Nodes));
                }
            }
            for (CandidateIndex, Candidate) in CandidateGroups[SecondIndex].iter().enumerate() {
                let Nodes = AggregateInitialConflictNodes(SecondIndex, Candidate);
                if !Nodes.is_empty() {
                    AlternativeOptions.push((Nodes.len(), SecondIndex, CandidateIndex, Nodes));
                }
            }
            AlternativeOptions.sort_by_key(|(NodeCount, GroupIndex, CandidateIndex, Nodes)| {
                (*NodeCount, *GroupIndex, *CandidateIndex, Nodes[0])
            });
            for (_NodeCount, GroupIndex, CandidateIndex, Nodes) in AlternativeOptions {
                if Deadline.Check() {
                    break;
                }
                let Candidate = &CandidateGroups[GroupIndex][CandidateIndex];
                let RequestIndex = Candidate.RequestIndex;
                let (
                    AccessIndex,
                    GuideIndex,
                    PreferredRoutingY,
                    GuidePenalty,
                    BendPenalty,
                    ViaPenalty,
                    MaximumExpansionCount,
                ) = Requests[RequestIndex];
                let RemainingExpansionCount = MaximumExpansionCount
                    .saturating_sub(RouteExpansionCountByRequest[RequestIndex]);
                if RemainingExpansionCount == 0 {
                    SawIncompleteRequest = true;
                    continue;
                }
                let NewNoGoodNodes = Nodes
                    .into_iter()
                    .filter(|Value| !AlternativeBlockedNodesByRequest[RequestIndex].contains(Value))
                    .collect::<Vec<_>>();
                if NewNoGoodNodes.is_empty() {
                    continue;
                }
                // Retain the original exact candidate, but make this
                // alternative leave the complete movable footprint of its
                // current cross-signal conflict.  Excluding one lexicographic
                // cell at a time merely translated the same path around the
                // next adjacent claim and consumed the bounded request share
                // without producing a physically distinct witness.
                AlternativeBlockedNodesByRequest[RequestIndex].extend(NewNoGoodNodes);
                let Access = &PreparedAccessPayloads[AccessIndex];
                let mut AlternativeBlockedNodes = Access.BlockedNodes.clone();
                AlternativeBlockedNodes.extend(
                    AlternativeBlockedNodesByRequest[RequestIndex]
                        .iter()
                        .copied(),
                );
                let Some(PreparedGuideIndex) = PreparedGuideIndexByKey
                    .get(&(GuideIndex, PreferredRoutingY, GuidePenalty))
                    .copied()
                else {
                    SawIncompleteRequest = true;
                    continue;
                };
                let SearchResult = Context.GenerateRouteTreeClaimAwarePreparedDetailedNative(
                    &Access.Starts,
                    &Access.TargetBranches,
                    &Access.FrozenTargetBranches,
                    &PreparedGuides[PreparedGuideIndex],
                    &Access.RequiredNodes,
                    &AlternativeBlockedNodes,
                    PreferredRoutingY,
                    BendPenalty,
                    ViaPenalty,
                    true,
                    Some(&Access.SourceBranch),
                    RemainingExpansionCount,
                    &Deadline,
                    &Access.MandatoryWire,
                    &Access.MandatorySupport,
                    &Access.MandatoryAir,
                    &SignalRequestIndices[GroupIndex].0,
                );
                GeneratedRequestCount += 1;
                RouteExpansionCountByRequest[RequestIndex] = RouteExpansionCountByRequest
                    [RequestIndex]
                    .saturating_add(SearchResult.ExpansionCount);
                if SearchResult.IsBudgetExpired {
                    SawIncompleteRequest = true;
                    break;
                }
                if SearchResult.IsRouted {
                    let Claims =
                        BuildExactSelectedWorldRouteClaims(Context, &SearchResult.Nodes, Access);
                    let NewCandidate = ExactSelectedWorldRouteCandidate {
                        RequestIndex,
                        Nodes: SearchResult.Nodes,
                        RepeaterReservations: SearchResult.RepeaterReservations,
                        Claims,
                    };
                    if !CandidateGroups[GroupIndex].iter().any(|Existing| {
                        Existing.Nodes == NewCandidate.Nodes
                            && Existing.RepeaterReservations == NewCandidate.RepeaterReservations
                    }) {
                        CandidateGroups[GroupIndex].push(NewCandidate);
                        AddedConflictAlternative = true;
                        break;
                    }
                }
            }
        }
        if AddedConflictAlternative {
            ReevaluateCandidateAssignment = true;
            continue;
        }
        if AssignmentExpansionCount >= EffectiveMaximumAssignmentExpansionCount {
            let CompletedWork = CompletionMask.iter().filter(|Value| **Value).count();
            return Ok(FactorizedRouteTreeSelectionResult {
                RouteTrees: vec![None; TotalWork],
                RepeaterReservations: vec![Vec::new(); TotalWork],
                CompletionMask,
                SelectedRequestIndices: Vec::new(),
                Success: false,
                Complete: false,
                DeadlineExceeded: false,
                WorkCapExceeded: true,
                AssignmentExpansionCount,
                GeneratedRequestCount,
                GeneratedRequestCountsBySignal: SignalRequestIndices
                    .iter()
                    .enumerate()
                    .map(|(GroupIndex, (Signal, _Requests))| {
                        (Signal.clone(), NextRequestOffsetByGroup[GroupIndex])
                    })
                    .collect(),
                CandidateCountsBySignal: SignalRequestIndices
                    .iter()
                    .enumerate()
                    .map(|(GroupIndex, (Signal, _Requests))| {
                        (Signal.clone(), CandidateGroups[GroupIndex].len())
                    })
                    .collect(),
                CompletedWork,
                TotalWork,
            });
        }
    }
}
