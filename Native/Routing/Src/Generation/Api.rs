use crate::Core::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Core::Models::{Position, RouteTreeBatchResult, RoutingContext};
use crate::Core::Runtime::RoutingThreadPool;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::HashSet;

#[cfg(test)]
use super::Batches::*;
#[cfg(test)]
use super::SelectedWorldClaims::*;
#[cfg(test)]
use std::collections::HashMap;

#[allow(clippy::type_complexity)]
pub(crate) fn GenerateRouteTreesNative(
    Context: &RoutingContext,
    Requests: Vec<(
        Vec<Position>,
        Vec<Vec<Position>>,
        Vec<(i32, i32)>,
        Vec<Position>,
        Vec<Position>,
        Vec<(i32, i32)>,
        i32,
        i32,
        i32,
        i32,
        usize,
    )>,
    MaximumRuntimeMilliseconds: Option<u64>,
) -> PyResult<RouteTreeBatchResult> {
    let Deadline = RuntimeDeadline::FromMilliseconds(MaximumRuntimeMilliseconds)
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let TotalWork = Requests.len();
    Deadline.Check();
    let WorkResults: Vec<(Option<Vec<Position>>, bool)> = RoutingThreadPool().install(|| {
        Requests
            .into_par_iter()
            .map(
                |(
                    Starts,
                    TargetBranches,
                    AllowedColumns,
                    RequiredNodes,
                    BlockedNodeValues,
                    PreferredColumns,
                    PreferredRoutingY,
                    GuidePenalty,
                    BendPenalty,
                    ViaPenalty,
                    MaximumExpansionCount,
                )| {
                    if Deadline.Check() {
                        return (None, false);
                    }
                    let mut AllowedNodes = HashSet::new();
                    for (Index, Column) in AllowedColumns.into_iter().enumerate() {
                        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                            return (None, false);
                        }
                        if let Some(Values) = Context.NodesByColumn.get(&Column) {
                            AllowedNodes.extend(Values.iter().copied());
                        }
                    }
                    AllowedNodes.extend(RequiredNodes);
                    let RouteTree = Context.GenerateRouteTreeNative(
                        Starts,
                        TargetBranches,
                        AllowedNodes.into_iter().collect(),
                        BlockedNodeValues,
                        PreferredColumns,
                        PreferredRoutingY,
                        GuidePenalty,
                        BendPenalty,
                        ViaPenalty,
                        MaximumExpansionCount,
                        Deadline
                            .RemainingMilliseconds()
                            .map(|Value| Value as f64 / 1_000.0),
                    );
                    let Completed = !Deadline.Check();
                    (RouteTree, Completed)
                },
            )
            .collect()
    });
    let CompletedWork = WorkResults
        .iter()
        .filter(|(_RouteTree, Completed)| *Completed)
        .count();
    let CompletionMask = WorkResults
        .iter()
        .map(|(_RouteTree, Completed)| *Completed)
        .collect();
    let RouteTrees = WorkResults
        .into_iter()
        .map(|(Value, _Completed)| Value)
        .collect();
    Ok(RouteTreeBatchResult {
        RouteTrees,
        RepeaterReservations: vec![Vec::new(); TotalWork],
        CompletionMask,
        DeadlineExceeded: Deadline.WasExceeded(),
        CompletedWork,
        TotalWork,
    })
}

#[cfg(test)]
mod Tests {
    use super::*;

    #[test]
    fn ForeignClaimKeepoutCoversExactBlockAndVerticalHeadroomConflicts() {
        let VerticalLower = (0, 0, 0);
        let VerticalUpper = (1, 1, 0);
        let Context = RoutingContext {
            Adjacency: HashMap::from([
                (VerticalLower, vec![VerticalUpper]),
                (VerticalUpper, vec![VerticalLower]),
            ]),
            NodesByColumn: HashMap::new(),
        };
        let Claims = ExactSelectedWorldRouteClaims {
            Wire: HashSet::from([(4, 0, 0), VerticalUpper]),
            Support: HashSet::from([(5, 0, 0), VerticalUpper]),
            Air: HashSet::from([(6, 0, 0)]),
            Electrical: HashSet::from([(7, 0, 0)]),
        };

        let Blocked = BuildExactSelectedWorldForeignBlockedNodes(&Context, [Claims]);

        assert!(Blocked.contains(&(5, 0, 0)));
        assert!(Blocked.contains(&(6, 0, 0)));
        assert!(Blocked.contains(&(7, 0, 0)));
        assert!(Blocked.contains(&(4, 1, 0)));
        assert!(Blocked.contains(&(6, 1, 0)));
        assert!(Blocked.contains(&VerticalUpper));
    }

    fn LinearContext() -> RoutingContext {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        RoutingContext {
            Adjacency: HashMap::from([(A, vec![B]), (B, vec![A])]),
            NodesByColumn: HashMap::from([((0, 0), vec![A]), ((1, 0), vec![B])]),
        }
    }

    #[test]
    fn PortalBatchReportsImmediateDeadlineAndNoCompletedWork() {
        let Result = GeneratePortalCandidateBatchesNative(
            &LinearContext(),
            vec![(
                vec![(0, 0, 0)],
                vec![(1, 0, 0)],
                vec![(0, 0, 0), (1, 0, 0)],
                0,
                4,
                128,
            )],
            Some(0),
        )
        .unwrap();
        assert!(Result.DeadlineExceeded);
        assert_eq!(Result.CompletedWork, 0);
        assert_eq!(Result.TotalWork, 1);
        assert_eq!(Result.Candidates.len(), 1);
        assert_eq!(Result.CompletionMask, vec![false]);
        assert_eq!(
            Result.CompletionMask.iter().filter(|Value| **Value).count(),
            Result.CompletedWork,
        );
    }

    #[test]
    fn CompactConnectivityBatchReportsReachableAndBlockedProofs() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let Results = LinearContext().CertifyRouteFactorConnectivityBatchNative(
            vec![
                (
                    vec![(0, 0), (1, 0)],
                    0,
                    Vec::new(),
                    Vec::new(),
                    vec![B],
                    A,
                    2,
                ),
                (vec![(0, 0), (1, 0)], 0, Vec::new(), vec![B], vec![B], A, 2),
            ],
            1_000,
        );
        assert_eq!(Results, vec![(true, true, 1), (true, false, 0)]);
    }

    #[test]
    fn CompactConnectivityBatchKeepsWorkAndDeadlineExhaustionIncomplete() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let C = (2, 0, 0);
        let Context = RoutingContext {
            Adjacency: HashMap::from([(A, vec![B]), (B, vec![A, C]), (C, vec![B])]),
            NodesByColumn: HashMap::from([((0, 0), vec![A]), ((1, 0), vec![B]), ((2, 0), vec![C])]),
        };
        let Request = (
            vec![(0, 0), (1, 0), (2, 0)],
            0,
            Vec::new(),
            Vec::new(),
            vec![C],
            A,
            1,
        );
        assert_eq!(
            Context.CertifyRouteFactorConnectivityBatchNative(vec![Request.clone()], 1_000,),
            vec![(false, false, 1)],
        );
        assert_eq!(
            Context.CertifyRouteFactorConnectivityBatchNative(vec![Request], 0,),
            vec![(false, false, 0)],
        );
    }

    #[test]
    fn RouteTreeBatchReportsImmediateDeadlineAndNoCompletedWork() {
        let Result = GenerateRouteTreesNative(
            &LinearContext(),
            vec![(
                vec![(0, 0, 0)],
                vec![vec![(1, 0, 0)]],
                vec![(0, 0), (1, 0)],
                Vec::new(),
                Vec::new(),
                Vec::new(),
                0,
                1,
                1,
                1,
                128,
            )],
            Some(0),
        )
        .unwrap();
        assert!(Result.DeadlineExceeded);
        assert_eq!(Result.CompletedWork, 0);
        assert_eq!(Result.TotalWork, 1);
        assert_eq!(Result.RouteTrees.len(), 1);
        assert_eq!(Result.CompletionMask, vec![false]);
    }

    #[test]
    fn DetailedRouteTreeBatchPreservesRequestOrderAndTypedResults() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let Result = GenerateRouteTreeDetailedBatchNative(
            &LinearContext(),
            vec![
                (
                    vec![A],
                    vec![vec![B]],
                    vec![A, B],
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    0,
                    0,
                    0,
                    0,
                    false,
                    128,
                ),
                (
                    vec![(99, 0, 0)],
                    vec![vec![B]],
                    vec![A, B],
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    0,
                    0,
                    0,
                    0,
                    false,
                    128,
                ),
            ],
            1_000,
        );

        assert_eq!(Result.TotalWork, 2);
        assert_eq!(Result.CompletedWork, 2);
        assert!(!Result.DeadlineExceeded);
        assert!(Result.SearchResults[0].IsRouted);
        assert_eq!(Result.SearchResults[1].Status, "NoPath");
        assert_eq!(Result.SearchResults[1].NoPathReason, "NoPathGeometry");
    }

    #[test]
    fn DetailedRouteTreeBatchReportsImmediateDeadlineForEveryRequest() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let Result = GenerateRouteTreeDetailedBatchNative(
            &LinearContext(),
            vec![
                (
                    vec![A],
                    vec![vec![B]],
                    vec![A, B],
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    0,
                    0,
                    0,
                    0,
                    false,
                    128,
                ),
                (
                    vec![A],
                    vec![vec![B]],
                    vec![A, B],
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    0,
                    0,
                    0,
                    0,
                    false,
                    128,
                ),
            ],
            0,
        );

        assert!(Result.DeadlineExceeded);
        assert_eq!(Result.CompletedWork, 0);
        assert_eq!(Result.TotalWork, 2);
        assert!(Result
            .SearchResults
            .iter()
            .all(|SearchResult| SearchResult.IsBudgetExpired));
    }

    #[test]
    fn RouteTreeIgnoresAllowedStartsOutsideResourceGraph() {
        let Context = LinearContext();
        let OffGraph = (99, 0, 0);
        let Result = Context.GenerateRouteTreeNative(
            vec![OffGraph, (0, 0, 0)],
            vec![vec![(1, 0, 0)]],
            vec![OffGraph, (0, 0, 0), (1, 0, 0)],
            Vec::new(),
            vec![(0, 0), (1, 0)],
            0,
            0,
            0,
            0,
            128,
            None,
        );

        assert_eq!(
            Result.map(|Values| Values.into_iter().collect::<HashSet<_>>()),
            Some(HashSet::from([(0, 0, 0), (1, 0, 0)])),
        );
    }

    #[test]
    fn RouteTreeBatchHonorsExplicitBlockedNodes() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let C = (0, 0, 1);
        let D = (1, 0, 1);
        let Context = RoutingContext {
            Adjacency: HashMap::from([
                (A, vec![B, C]),
                (B, vec![A, D]),
                (C, vec![A, D]),
                (D, vec![B, C]),
            ]),
            NodesByColumn: HashMap::from([
                ((0, 0), vec![A]),
                ((1, 0), vec![B]),
                ((0, 1), vec![C]),
                ((1, 1), vec![D]),
            ]),
        };
        let AllowedColumns = vec![(0, 0), (1, 0), (0, 1), (1, 1)];
        let Result = GenerateRouteTreesNative(
            &Context,
            vec![
                (
                    vec![B, A],
                    vec![vec![D]],
                    AllowedColumns.clone(),
                    Vec::new(),
                    vec![B],
                    Vec::new(),
                    0,
                    0,
                    0,
                    0,
                    128,
                ),
                (
                    vec![A],
                    vec![vec![D]],
                    AllowedColumns,
                    Vec::new(),
                    vec![D],
                    Vec::new(),
                    0,
                    0,
                    0,
                    0,
                    128,
                ),
            ],
            None,
        )
        .unwrap();

        assert_eq!(Result.RouteTrees[0], Some(vec![A, C, D]));
        assert_eq!(Result.RouteTrees[1], None);
        assert_eq!(Result.CompletionMask, vec![true, true]);
        assert_eq!(Result.CompletedWork, 2);
        assert!(!Result.DeadlineExceeded);
    }

    #[test]
    fn PortalIncludesGraphAccessAnchorAndAdjacentPath() {
        let Context = LinearContext();
        let Start = (0, 0, 0);
        let Target = (1, 0, 0);
        let Values = Context.GeneratePortalCandidatesNative(
            vec![(99, 0, 0), Start],
            vec![Target],
            vec![(99, 0, 0), Start, Target],
            0,
            4,
            128,
            None,
        );

        assert_eq!(Values.len(), 1);
        assert_eq!(Values[0].Path, vec![Start, Target]);
        assert!(Values[0].WireClaims.contains(&Start));
        assert!(Values[0].Path.windows(2).all(|Values| Context
            .Adjacency
            .get(&Values[0])
            .map(|Neighbors| Neighbors.contains(&Values[1]))
            .unwrap_or(false)));
    }

    #[test]
    fn ClaimAwareTreeRejectsCheapestSelfConflictAndKeepsLaterPath() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let C = (0, 0, 1);
        let D = (1, 0, 1);
        let Context = RoutingContext {
            Adjacency: HashMap::from([
                (A, vec![B, C]),
                (B, vec![A, D]),
                (C, vec![A, D]),
                (D, vec![B, C]),
            ]),
            NodesByColumn: HashMap::new(),
        };
        let Result = Context.GenerateRouteTreeClaimAwareDetailedNative(
            vec![A],
            vec![vec![D]],
            vec![A, B, C, D],
            Vec::new(),
            Vec::new(),
            Vec::new(),
            0,
            0,
            0,
            0,
            false,
            128,
            1_000,
            Vec::new(),
            vec![B],
            Vec::new(),
            Vec::new(),
        );

        assert!(Result.IsRouted);
        assert_eq!(Result.Nodes, vec![A, C, D]);
        assert_eq!(Result.RejectedPathCount, 1);
        assert_eq!(Result.NoGoodCount, 1);
        assert!(Result.ConflictResources.is_empty());
    }

    #[test]
    fn ClaimAwareTreeReportsCompleteStaticSelfConflict() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let Result = LinearContext().GenerateRouteTreeClaimAwareDetailedNative(
            vec![A],
            vec![vec![B]],
            vec![A, B],
            Vec::new(),
            Vec::new(),
            Vec::new(),
            0,
            0,
            0,
            0,
            false,
            128,
            1_000,
            Vec::new(),
            vec![B],
            Vec::new(),
            Vec::new(),
        );

        assert_eq!(Result.Status, "NoPath");
        assert_eq!(Result.NoPathReason, "SelfClaimConflict");
        assert_eq!(Result.RejectedPathCount, 1);
        assert!(!Result.ConflictResources.is_empty());
    }
}
