use crate::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Models::{
    PortalCandidate, PortalCandidateBatchResult, Position, RouteTreeBatchResult, RoutingContext,
};
use crate::PathRouting::{
    BuildPortalCandidate, FindPathWithDeadline, ManhattanDistance, NormalizeEdge, BLOCKED_EDGE_COST,
};
use crate::RoutingThreadPool;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::{HashMap, HashSet};

impl RoutingContext {
    pub(crate) fn GeneratePortalCandidatesNative(
        &self,
        Starts: Vec<Position>,
        PortalTargets: Vec<Position>,
        AllowedNodeValues: Vec<Position>,
        PreferredRoutingY: i32,
        MaximumPortalCount: usize,
        MaximumExpansionCount: usize,
        MaximumRuntimeSeconds: Option<f64>,
    ) -> Vec<PortalCandidate> {
        let Ok(Deadline) = RuntimeDeadline::FromSeconds(MaximumRuntimeSeconds) else {
            return Vec::new();
        };
        if Deadline.Check() {
            return Vec::new();
        }
        let mut AllowedNodes = HashSet::with_capacity(AllowedNodeValues.len());
        for (Index, Value) in AllowedNodeValues.into_iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return Vec::new();
            }
            AllowedNodes.insert(Value);
        }
        let Starts: Vec<_> = Starts
            .into_iter()
            .filter(|Value| AllowedNodes.contains(Value) && self.Adjacency.contains_key(Value))
            .collect();
        if Starts.is_empty() {
            return Vec::new();
        }
        let mut BlockedNodes = HashSet::new();
        for (Index, Value) in AllowedNodes.iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return Vec::new();
            }
            for Neighbor in self.Adjacency.get(Value).into_iter().flatten() {
                if !AllowedNodes.contains(Neighbor) {
                    BlockedNodes.insert(*Neighbor);
                }
            }
        }
        let mut Targets = PortalTargets;
        Targets.sort_unstable();
        Targets.dedup();
        let mut Candidates = Vec::new();
        for Target in Targets {
            if Deadline.Check() {
                return Vec::new();
            }
            let Some(mut Path) = FindPathWithDeadline(
                &self.Adjacency,
                &Starts,
                Target,
                PreferredRoutingY,
                &BlockedNodes,
                &HashMap::new(),
                &HashMap::new(),
                1,
                1,
                0,
                MaximumExpansionCount,
                &Deadline,
            ) else {
                continue;
            };
            let Some(FirstStep) = Path.first().copied() else {
                continue;
            };
            if !Starts.contains(&FirstStep) {
                let Some(AccessAnchor) = Starts.iter().copied().find(|Start| {
                    self.Adjacency
                        .get(Start)
                        .map(|Neighbors| Neighbors.contains(&FirstStep))
                        .unwrap_or(false)
                }) else {
                    continue;
                };
                Path.insert(0, AccessAnchor);
            }
            let Candidate = BuildPortalCandidate(
                format!("Portal:{},{},{}", Target.0, Target.1, Target.2),
                Target,
                Path,
            );
            let WireSet: HashSet<_> = Candidate.WireClaims.iter().copied().collect();
            let IsDominated = Candidates.iter().any(|Existing: &PortalCandidate| {
                Existing.Length <= Candidate.Length
                    && Existing.BendCount <= Candidate.BendCount
                    && Existing.ViaCount <= Candidate.ViaCount
                    && Existing
                        .WireClaims
                        .iter()
                        .all(|Value| WireSet.contains(Value))
            });
            if !IsDominated {
                Candidates.push(Candidate);
            }
        }
        Candidates.sort_by_key(|Value| {
            (
                Value.Length,
                Value.BendCount,
                Value.ViaCount,
                Value.Target,
                Value.PortalId.clone(),
            )
        });
        Candidates.truncate(MaximumPortalCount.clamp(1, 64));
        Candidates
    }

    pub(crate) fn GenerateRouteTreeNative(
        &self,
        Starts: Vec<Position>,
        TargetBranches: Vec<Vec<Position>>,
        AllowedNodeValues: Vec<Position>,
        BlockedNodeValues: Vec<Position>,
        PreferredColumns: Vec<(i32, i32)>,
        PreferredRoutingY: i32,
        GuidePenalty: i32,
        BendPenalty: i32,
        ViaPenalty: i32,
        MaximumExpansionCount: usize,
        MaximumRuntimeSeconds: Option<f64>,
    ) -> Option<Vec<Position>> {
        self.GenerateRouteTreeWithCostsNative(
            Starts,
            TargetBranches,
            AllowedNodeValues,
            BlockedNodeValues,
            PreferredColumns,
            Vec::new(),
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            MaximumExpansionCount,
            MaximumRuntimeSeconds,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn GenerateRouteTreeWithCostsNative(
        &self,
        Starts: Vec<Position>,
        TargetBranches: Vec<Vec<Position>>,
        AllowedNodeValues: Vec<Position>,
        BlockedNodeValues: Vec<Position>,
        PreferredColumns: Vec<(i32, i32)>,
        ExternalNodeCostValues: Vec<(Position, i32)>,
        PreferredRoutingY: i32,
        GuidePenalty: i32,
        BendPenalty: i32,
        ViaPenalty: i32,
        MaximumExpansionCount: usize,
        MaximumRuntimeSeconds: Option<f64>,
    ) -> Option<Vec<Position>> {
        let Ok(Deadline) = RuntimeDeadline::FromSeconds(MaximumRuntimeSeconds) else {
            return None;
        };
        if Deadline.Check() {
            return None;
        }
        let mut AllowedNodes = HashSet::with_capacity(AllowedNodeValues.len());
        for (Index, Value) in AllowedNodeValues.into_iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            AllowedNodes.insert(Value);
        }
        let mut ExplicitBlockedNodes = HashSet::with_capacity(BlockedNodeValues.len());
        for (Index, Value) in BlockedNodeValues.into_iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            ExplicitBlockedNodes.insert(Value);
        }
        if TargetBranches.iter().any(|Branch| {
            Branch
                .iter()
                .any(|Value| ExplicitBlockedNodes.contains(Value))
        }) {
            return None;
        }
        let mut BlockedNodes = ExplicitBlockedNodes;
        for (Index, Value) in AllowedNodes.iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            for Neighbor in self.Adjacency.get(Value).into_iter().flatten() {
                if !AllowedNodes.contains(Neighbor) {
                    BlockedNodes.insert(*Neighbor);
                }
            }
        }
        let mut NodeCosts: HashMap<Position, i32> = ExternalNodeCostValues
            .into_iter()
            .filter(|(_PositionValue, Cost)| *Cost > 0)
            .collect();
        if GuidePenalty > 0 && !PreferredColumns.is_empty() {
            for (Index, Value) in AllowedNodes.iter().enumerate() {
                if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
                let Distance = PreferredColumns
                    .iter()
                    .map(|Column| (Value.0 - Column.0).abs() + (Value.2 - Column.1).abs())
                    .min()
                    .unwrap_or(0);
                *NodeCosts.entry(*Value).or_default() += Distance * GuidePenalty;
            }
        }
        let Starts: Vec<_> = Starts
            .into_iter()
            .filter(|Value| {
                AllowedNodes.contains(Value)
                    && self.Adjacency.contains_key(Value)
                    && !BlockedNodes.contains(Value)
            })
            .collect();
        if Starts.is_empty() {
            return None;
        }

        let RootStart = Starts[0];
        let mut Tree: HashSet<_> = HashSet::new();
        for Start in Starts {
            if Deadline.Check() {
                return None;
            }
            Tree.insert(Start);
        }
        let BranchTargets: Vec<Position> = TargetBranches
            .iter()
            .filter_map(|Branch| Branch.last().copied())
            .collect();

        let BuildRootComponent = |Tree: &HashSet<Position>| -> HashSet<Position> {
            let mut Stack = Vec::new();
            let mut Component = HashSet::new();
            if !Tree.contains(&RootStart) {
                return Component;
            }
            Component.insert(RootStart);
            Stack.push(RootStart);
            while let Some(Value) = Stack.pop() {
                if Component.len() % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return HashSet::new();
                }
                let Neighbors = self.Adjacency.get(&Value).into_iter().flatten();
                for Neighbor in Neighbors {
                    if !Tree.contains(Neighbor) || Component.contains(Neighbor) {
                        continue;
                    }
                    Component.insert(*Neighbor);
                    Stack.push(*Neighbor);
                }
            }
            Component
        };

        let mut RootComponent = BuildRootComponent(&Tree);
        if RootComponent.is_empty() {
            return None;
        }

        let StartsToConnect: Vec<Position> = Tree
            .iter()
            .filter(|Value| !RootComponent.contains(Value))
            .copied()
            .collect();
        if !StartsToConnect.is_empty() {
            for Start in StartsToConnect {
                if Deadline.Check() {
                    return None;
                }
                let TreeStarts: Vec<_> = RootComponent.iter().copied().collect();
                let PathToStart = FindPathWithDeadline(
                    &self.Adjacency,
                    &TreeStarts,
                    Start,
                    PreferredRoutingY,
                    &BlockedNodes,
                    &NodeCosts,
                    &HashMap::new(),
                    BendPenalty,
                    ViaPenalty,
                    0,
                    MaximumExpansionCount,
                    &Deadline,
                )?;
                if PathToStart.is_empty() {
                    return None;
                }
                Tree.extend(PathToStart);
                RootComponent = BuildRootComponent(&Tree);
                if RootComponent.is_empty() {
                    return None;
                }
            }
        }

        let IsBranchChain = |Branch: &Vec<Position>| -> bool {
            Branch.windows(2).all(|Window| {
                self.Adjacency.contains_key(&Window[0])
                    && self.Adjacency[&Window[0]].contains(&Window[1])
            })
        };

        let IsConnectedToRoot = |Target: &Position, RootComponent: &HashSet<Position>| -> bool {
            RootComponent.contains(Target)
        };

        let mut RemainingBranches = TargetBranches;
        while !RemainingBranches.is_empty() {
            if Deadline.Check() {
                return None;
            }
            let SelectedIndex = RemainingBranches
                .iter()
                .enumerate()
                .min_by_key(|(_, Branch)| {
                    let Target = Branch
                        .last()
                        .copied()
                        .unwrap_or((i32::MAX, i32::MAX, i32::MAX));
                    let Distance = RootComponent
                        .iter()
                        .map(|Start| ManhattanDistance(*Start, Target))
                        .min()
                        .unwrap_or(i32::MAX);
                    (Distance, Target, Branch.len())
                })
                .map(|(Index, _)| Index)?;
            let Branch = RemainingBranches.remove(SelectedIndex);
            let Target = *Branch.first()?;
            if !IsBranchChain(&Branch) {
                return None;
            }

            let mut TreeStarts: Vec<_> = RootComponent.iter().copied().collect();
            TreeStarts.sort_unstable();
            let ExistingSupport: HashSet<_> = Tree
                .iter()
                .map(|Value| (Value.0, Value.1 - 1, Value.2))
                .collect();
            let mut ExistingAir = HashSet::new();
            for (Index, First) in Tree.iter().enumerate() {
                if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
                for Second in self.Adjacency.get(First).into_iter().flatten() {
                    if !Tree.contains(Second) || Second <= First || First.1 == Second.1 {
                        continue;
                    }
                    let Lower = if First.1 < Second.1 { *First } else { *Second };
                    ExistingAir.insert((Lower.0, Lower.1 + 1, Lower.2));
                }
            }
            let BranchNodes: HashSet<_> = Branch.iter().copied().collect();
            let BranchSupport: HashSet<_> = BranchNodes
                .iter()
                .map(|Value| (Value.0, Value.1 - 1, Value.2))
                .collect();
            let mut BranchAir = HashSet::new();
            for Values in Branch.windows(2) {
                if Values[0].1 != Values[1].1 {
                    let Lower = if Values[0].1 < Values[1].1 {
                        Values[0]
                    } else {
                        Values[1]
                    };
                    BranchAir.insert((Lower.0, Lower.1 + 1, Lower.2));
                }
            }
            let mut DynamicBlocked = BlockedNodes.clone();
            for (Index, Node) in AllowedNodes.iter().enumerate() {
                if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
                let Support = (Node.0, Node.1 - 1, Node.2);
                if ExistingSupport.contains(Node)
                    || ExistingAir.contains(Node)
                    || BranchSupport.contains(Node)
                    || BranchAir.contains(Node)
                    || Tree.contains(&Support)
                    || ExistingAir.contains(&Support)
                    || BranchNodes.contains(&Support)
                    || BranchAir.contains(&Support)
                {
                    DynamicBlocked.insert(*Node);
                }
            }
            DynamicBlocked.remove(&Target);
            let mut DynamicEdgeCosts = HashMap::new();
            for (Index, First) in AllowedNodes.iter().enumerate() {
                if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
                for Second in self.Adjacency.get(First).into_iter().flatten() {
                    if !AllowedNodes.contains(Second) || Second <= First || First.1 == Second.1 {
                        continue;
                    }
                    let Lower = if First.1 < Second.1 { *First } else { *Second };
                    let Headroom = (Lower.0, Lower.1 + 1, Lower.2);
                    if Tree.contains(&Headroom)
                        || ExistingSupport.contains(&Headroom)
                        || BranchNodes.contains(&Headroom)
                        || BranchSupport.contains(&Headroom)
                    {
                        DynamicEdgeCosts.insert(NormalizeEdge(*First, *Second), BLOCKED_EDGE_COST);
                    }
                }
            }
            let NeedsPath = !IsConnectedToRoot(&Target, &RootComponent);
            if !Tree.contains(&Target) || NeedsPath {
                let Path = FindPathWithDeadline(
                    &self.Adjacency,
                    &TreeStarts,
                    Target,
                    PreferredRoutingY,
                    &DynamicBlocked,
                    &NodeCosts,
                    &DynamicEdgeCosts,
                    BendPenalty,
                    ViaPenalty,
                    0,
                    MaximumExpansionCount,
                    &Deadline,
                )?;
                Tree.extend(Path);
            } else if Branch.len() > 1 {
                let ExistingInBranch: HashSet<_> = Branch
                    .iter()
                    .filter(|Value| Tree.contains(Value))
                    .copied()
                    .collect();
                if ExistingInBranch.len() > 1 {
                    return None;
                }
            }

            Tree.extend(Branch);
            RootComponent = BuildRootComponent(&Tree);
            if RootComponent.is_empty() {
                return None;
            }
        }

        if !BranchTargets
            .iter()
            .all(|Target| IsConnectedToRoot(Target, &RootComponent))
        {
            return None;
        }

        let mut Result: Vec<_> = Tree.into_iter().collect();
        Result.sort_unstable();
        Some(Result)
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
    let Candidates = WorkResults
        .into_iter()
        .map(|(Values, _Completed)| Values)
        .collect();
    Ok(PortalCandidateBatchResult {
        Candidates,
        DeadlineExceeded: Deadline.WasExceeded(),
        CompletedWork,
        TotalWork,
    })
}

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
    let RouteTrees = WorkResults
        .into_iter()
        .map(|(Value, _Completed)| Value)
        .collect();
    Ok(RouteTreeBatchResult {
        RouteTrees,
        DeadlineExceeded: Deadline.WasExceeded(),
        CompletedWork,
        TotalWork,
    })
}

#[cfg(test)]
mod Tests {
    use super::*;

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
}
