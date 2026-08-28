use crate::Core::Deadline::RuntimeDeadline;
use crate::Core::Models::{
    ClaimAwareDetailedRouteTreeRequest, DetailedRouteTreeRequest, PortalCandidate,
    PortalCandidateBatchResult, Position, RouteTreeDetailedBatchResult, RouteTreeSearchResult,
    RoutingContext,
};
use crate::Core::Runtime::RoutingThreadPool;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::time::Instant;

use super::DetailedTrees::*;

pub(crate) fn GenerateRouteTreeClaimAwareDetailedBatchNative(
    Context: &RoutingContext,
    Requests: Vec<ClaimAwareDetailedRouteTreeRequest>,
    MaximumRuntimeMilliseconds: u64,
) -> RouteTreeDetailedBatchResult {
    let Started = Instant::now();
    let SearchResults: Vec<RouteTreeSearchResult> = RoutingThreadPool().install(|| {
        Requests
            .into_par_iter()
            .map(
                |(
                    (
                        Starts,
                        TargetBranches,
                        AllowedNodes,
                        BlockedNodes,
                        PreferredColumns,
                        NodeCosts,
                        PreferredRoutingY,
                        GuidePenalty,
                        BendPenalty,
                        ViaPenalty,
                        EnforceSignalStrength,
                        MaximumExpansionCount,
                    ),
                    (MandatoryWire, MandatorySupport, MandatoryAir, MandatoryElectrical),
                )| {
                    let Elapsed = Started.elapsed().as_millis() as u64;
                    let Remaining = MaximumRuntimeMilliseconds.saturating_sub(Elapsed);
                    if Remaining == 0 {
                        return DetailedRouteTreeBudgetExpiredResult();
                    }
                    Context.GenerateRouteTreeClaimAwareDetailedNative(
                        Starts,
                        TargetBranches,
                        AllowedNodes,
                        BlockedNodes,
                        PreferredColumns,
                        NodeCosts,
                        PreferredRoutingY,
                        GuidePenalty,
                        BendPenalty,
                        ViaPenalty,
                        EnforceSignalStrength,
                        MaximumExpansionCount,
                        Remaining,
                        MandatoryWire,
                        MandatorySupport,
                        MandatoryAir,
                        MandatoryElectrical,
                    )
                },
            )
            .collect()
    });
    let CompletionMask: Vec<_> = SearchResults
        .iter()
        .map(|Value| !Value.IsBudgetExpired)
        .collect();
    let CompletedWork = CompletionMask.iter().filter(|Value| **Value).count();
    RouteTreeDetailedBatchResult {
        DeadlineExceeded: CompletionMask.iter().any(|Value| !*Value),
        CompletedWork,
        TotalWork: CompletionMask.len(),
        SearchResults,
    }
}

/// Generates independent repeater-aware route trees against one immutable
/// negotiated-pass snapshot.  The shared deadline is deliberately passed into
/// every search rather than split into per-worker budgets: a queued request
/// cannot consume time after the pass has expired, and a completed result
/// remains at the same input index regardless of worker scheduling.
pub(crate) fn GenerateRouteTreeDetailedBatchNative(
    Context: &RoutingContext,
    Requests: Vec<DetailedRouteTreeRequest>,
    MaximumRuntimeMilliseconds: u64,
) -> RouteTreeDetailedBatchResult {
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .expect("u64 millisecond deadlines must be representable");
    let TotalWork = Requests.len();
    let SearchResults: Vec<RouteTreeSearchResult> = RoutingThreadPool().install(|| {
        Requests
            .into_par_iter()
            .map(
                |(
                    Starts,
                    TargetBranches,
                    AllowedNodeValues,
                    BlockedNodeValues,
                    PreferredColumns,
                    NodeCostValues,
                    PreferredRoutingY,
                    GuidePenalty,
                    BendPenalty,
                    ViaPenalty,
                    EnforceSignalStrength,
                    MaximumExpansionCount,
                )| {
                    if Deadline.Check() {
                        return DetailedRouteTreeBudgetExpiredResult();
                    }
                    Context.GenerateRouteTreeDetailedWithDeadlineNative(
                        Starts,
                        TargetBranches,
                        AllowedNodeValues,
                        BlockedNodeValues,
                        PreferredColumns,
                        NodeCostValues,
                        PreferredRoutingY,
                        GuidePenalty,
                        BendPenalty,
                        ViaPenalty,
                        EnforceSignalStrength,
                        MaximumExpansionCount,
                        &Deadline,
                    )
                },
            )
            .collect()
    });
    let CompletedWork = SearchResults
        .iter()
        .filter(|Result| !Result.IsBudgetExpired)
        .count();
    RouteTreeDetailedBatchResult {
        SearchResults,
        DeadlineExceeded: Deadline.WasExceeded(),
        CompletedWork,
        TotalWork,
    }
}

#[allow(clippy::type_complexity)]
pub(crate) fn GeneratePortalCandidateBatchesNative(
    Context: &RoutingContext,
    Requests: Vec<(
        Vec<Position>,
        Vec<Position>,
        Vec<Position>,
        i32,
        usize,
        usize,
    )>,
    MaximumRuntimeMilliseconds: Option<u64>,
) -> PyResult<PortalCandidateBatchResult> {
    let Deadline = RuntimeDeadline::FromMilliseconds(MaximumRuntimeMilliseconds)
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let TotalWork = Requests.len();
    Deadline.Check();
    let WorkResults: Vec<(Vec<PortalCandidate>, bool)> = RoutingThreadPool().install(|| {
        Requests
            .into_par_iter()
            .map(
                |(
                    Starts,
                    PortalTargets,
                    AllowedNodes,
                    PreferredRoutingY,
                    MaximumPortalCount,
                    MaximumExpansionCount,
                )| {
                    if Deadline.Check() {
                        return (Vec::new(), false);
                    }
                    let Candidates = Context.GeneratePortalCandidatesNative(
                        Starts,
                        PortalTargets,
                        AllowedNodes,
                        PreferredRoutingY,
                        MaximumPortalCount,
                        MaximumExpansionCount,
                        Deadline
                            .RemainingMilliseconds()
                            .map(|Value| Value as f64 / 1_000.0),
                    );
                    let Completed = !Deadline.Check();
                    (Candidates, Completed)
                },
            )
            .collect()
    });
    let CompletedWork = WorkResults
        .iter()
        .filter(|(_Candidates, Completed)| *Completed)
        .count();
    let CompletionMask = WorkResults
        .iter()
        .map(|(_Candidates, Completed)| *Completed)
        .collect();
    let Candidates = WorkResults
        .into_iter()
        .map(|(Values, _Completed)| Values)
        .collect();
    Ok(PortalCandidateBatchResult {
        Candidates,
        CompletionMask,
        DeadlineExceeded: Deadline.WasExceeded(),
        CompletedWork,
        TotalWork,
    })
}
