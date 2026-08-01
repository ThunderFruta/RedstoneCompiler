use crate::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Models::{
    DetailedRouteTreeRequest, PortalCandidate, PortalCandidateBatchResult, Position,
    RouteTreeBatchResult, RouteTreeDetailedBatchResult, RouteTreeSearchResult, RoutingContext,
    SearchState,
};
use crate::PathRouting::{
    BuildPortalCandidate, FindPathFromStatesDetailedWithDeadline, FindPathWithDeadline,
    ManhattanDistance, NormalizeEdge, BLOCKED_EDGE_COST, MAXIMUM_UNREFRESHED_DUST_LENGTH,
};
use crate::RoutingThreadPool;
use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::{HashMap, HashSet};

fn DetailedRouteTreeBudgetExpiredResult() -> RouteTreeSearchResult {
    RouteTreeSearchResult {
        Status: "BudgetExpired".to_string(),
        NoPathReason: "BudgetExpired".to_string(),
        Nodes: Vec::new(),
        TargetPaths: Vec::new(),
        BoundaryFrontierNodes: Vec::new(),
        RepeaterReservations: Vec::new(),
        ExpansionCount: 0,
        RepeaterRejectedCount: 0,
        RepeaterConstraintFailureCount: 0,
        IsRouted: false,
        IsBudgetExpired: true,
    }
}

fn BuildRootedTreeBlockages(
    Context: &RoutingContext,
    AllowedNodes: &HashSet<Position>,
    BaseBlockedNodes: &HashSet<Position>,
    Tree: &HashSet<Position>,
    ReservedNodes: &HashSet<Position>,
    Deadline: &RuntimeDeadline,
) -> Option<(HashSet<Position>, HashMap<(Position, Position), i32>)> {
    let OccupiedNodes: HashSet<_> = Tree.union(ReservedNodes).copied().collect();
    let Supports: HashSet<_> = OccupiedNodes
        .iter()
        .map(|Value| (Value.0, Value.1 - 1, Value.2))
        .collect();
    let mut RequiredAir = HashSet::new();
    for (Index, First) in OccupiedNodes.iter().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return None;
        }
        for Second in Context.Adjacency.get(First).into_iter().flatten() {
            if !OccupiedNodes.contains(Second) || Second <= First || First.1 == Second.1 {
                continue;
            }
            let Lower = if First.1 < Second.1 { *First } else { *Second };
            RequiredAir.insert((Lower.0, Lower.1 + 1, Lower.2));
        }
    }
    let mut BlockedNodes = BaseBlockedNodes.clone();
    let LocalBlockedNodes = Supports.iter().chain(RequiredAir.iter()).copied().chain(
        OccupiedNodes
            .iter()
            .chain(RequiredAir.iter())
            .map(|Value| (Value.0, Value.1 + 1, Value.2)),
    );
    for (Index, Node) in LocalBlockedNodes.enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return None;
        }
        if AllowedNodes.contains(&Node) {
            BlockedNodes.insert(Node);
        }
    }
    let mut EdgeCosts = HashMap::new();
    for (Index, Headroom) in OccupiedNodes.iter().chain(Supports.iter()).enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return None;
        }
        let Lower = (Headroom.0, Headroom.1 - 1, Headroom.2);
        if !AllowedNodes.contains(&Lower) {
            continue;
        }
        for Second in Context.Adjacency.get(&Lower).into_iter().flatten() {
            if !AllowedNodes.contains(Second) || Lower.1 == Second.1 {
                continue;
            }
            let EdgeLower = if Lower.1 < Second.1 { Lower } else { *Second };
            let EdgeHeadroom = (EdgeLower.0, EdgeLower.1 + 1, EdgeLower.2);
            if EdgeHeadroom == *Headroom {
                EdgeCosts.insert(NormalizeEdge(Lower, *Second), BLOCKED_EDGE_COST);
            }
        }
    }
    Some((BlockedNodes, EdgeCosts))
}

fn RepeaterFacing(Current: Position, Next: Position) -> Option<String> {
    match (Next.0 - Current.0, Next.2 - Current.2) {
        (1, 0) => Some("west".to_string()),
        (-1, 0) => Some("east".to_string()),
        (0, 1) => Some("north".to_string()),
        (0, -1) => Some("south".to_string()),
        _ => None,
    }
}

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
                false,
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
            false,
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
        EnforceSignalStrength: bool,
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
                    EnforceSignalStrength,
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
                    EnforceSignalStrength,
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

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn GenerateRouteTreeDetailedNative(
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
        EnforceSignalStrength: bool,
        MaximumExpansionCount: usize,
        MaximumRuntimeMilliseconds: u64,
    ) -> RouteTreeSearchResult {
        let Ok(Deadline) =
            RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds.max(1)))
        else {
            return DetailedRouteTreeBudgetExpiredResult();
        };
        self.GenerateRouteTreeDetailedWithDeadlineNative(
            Starts,
            TargetBranches,
            AllowedNodeValues,
            BlockedNodeValues,
            PreferredColumns,
            ExternalNodeCostValues,
            PreferredRoutingY,
            GuidePenalty,
            BendPenalty,
            ViaPenalty,
            EnforceSignalStrength,
            MaximumExpansionCount,
            &Deadline,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn GenerateRouteTreeDetailedWithDeadlineNative(
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
        EnforceSignalStrength: bool,
        MaximumExpansionCount: usize,
        Deadline: &RuntimeDeadline,
    ) -> RouteTreeSearchResult {
        let Failure = |Status: &str,
                       NoPathReason: &str,
                       RepeaterRejectedCount: usize,
                       RepeaterConstraintFailureCount: usize,
                       ExpansionCount: usize| {
            let Expired = Deadline.Check();
            let EffectiveStatus = if Expired { "BudgetExpired" } else { Status };
            RouteTreeSearchResult {
                Status: EffectiveStatus.to_string(),
                NoPathReason: NoPathReason.to_string(),
                Nodes: Vec::new(),
                TargetPaths: Vec::new(),
                BoundaryFrontierNodes: Vec::new(),
                RepeaterReservations: Vec::new(),
                ExpansionCount,
                RepeaterRejectedCount,
                RepeaterConstraintFailureCount,
                IsRouted: false,
                IsBudgetExpired: Expired,
            }
        };
        let AllowedNodes: HashSet<_> = AllowedNodeValues.into_iter().collect();
        let mut BlockedNodes: HashSet<_> = BlockedNodeValues.into_iter().collect();
        for Value in &AllowedNodes {
            for Neighbor in self.Adjacency.get(Value).into_iter().flatten() {
                if !AllowedNodes.contains(Neighbor) {
                    BlockedNodes.insert(*Neighbor);
                }
            }
        }
        if TargetBranches.iter().any(|Branch| {
            Branch.is_empty()
                || Branch.windows(2).any(|Values| {
                    !self
                        .Adjacency
                        .get(&Values[0])
                        .is_some_and(|Neighbors| Neighbors.contains(&Values[1]))
                })
                || Branch.iter().any(|Value| BlockedNodes.contains(Value))
        }) {
            return Failure("NoPath", "NoPathGeometry", 0, 0, 0);
        }
        let mut NodeCosts: HashMap<_, _> = ExternalNodeCostValues
            .into_iter()
            .filter(|(_PositionValue, Cost)| *Cost > 0)
            .collect();
        if GuidePenalty > 0 && !PreferredColumns.is_empty() {
            for Value in &AllowedNodes {
                if Deadline.Check() {
                    return Failure("NoPath", "SearchLimitReached", 0, 0, 0);
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
        let Some(Root) = Starts.first().copied() else {
            return Failure("NoPath", "NoPathGeometry", 0, 0, 0);
        };

        let StartDirection = (0, 0, 0);
        let RootState = (Root, StartDirection, MAXIMUM_UNREFRESHED_DUST_LENGTH);
        let mut Tree = HashSet::from([Root]);
        let mut StateByNode = HashMap::from([(Root, RootState)]);
        let mut ParentByNode: HashMap<Position, Position> = HashMap::new();
        let mut Repeaters: HashMap<Position, String> = HashMap::new();
        let mut ExpansionCount = 0usize;

        let RouteIntoTree = |Target: Position,
                             TargetContinuation: &[Position],
                             ReservedNodes: &HashSet<Position>,
                             TreeValue: &HashSet<Position>,
                             States: &HashMap<Position, SearchState>|
         -> Option<crate::PathRouting::PathSearchResult> {
            let (mut DynamicBlocked, EdgeCosts) = BuildRootedTreeBlockages(
                self,
                &AllowedNodes,
                &BlockedNodes,
                TreeValue,
                ReservedNodes,
                &Deadline,
            )?;
            DynamicBlocked.remove(&Target);
            let mut StartStates: Vec<_> = States.values().copied().collect();
            StartStates.sort_unstable();
            FindPathFromStatesDetailedWithDeadline(
                &self.Adjacency,
                &StartStates,
                Target,
                PreferredRoutingY,
                &DynamicBlocked,
                &NodeCosts,
                &EdgeCosts,
                BendPenalty,
                ViaPenalty,
                0,
                MaximumExpansionCount,
                EnforceSignalStrength,
                TargetContinuation,
                0,
                &Deadline,
            )
        };

        // Source access and retained repair trunks are mandatory rooted tree
        // nodes. Connect them under their actual incoming direction and
        // remaining strength instead of granting every node fresh power.
        for Start in Starts.into_iter().skip(1) {
            if Tree.contains(&Start) {
                continue;
            }
            let Some(Result) = RouteIntoTree(Start, &[], &HashSet::new(), &Tree, &StateByNode)
            else {
                return Failure(
                    "NoPath",
                    "NoPathGeometry",
                    usize::from(EnforceSignalStrength),
                    0,
                    ExpansionCount,
                );
            };
            if Result.Status != "Routed" {
                return Failure(
                    "NoPath",
                    if Result.NoPathReason.is_empty() {
                        "NoPathGeometry"
                    } else {
                        &Result.NoPathReason
                    },
                    Result.RepeaterRejectedCount,
                    Result.RepeaterConstraintFailures,
                    ExpansionCount + Result.ExpansionCount,
                );
            };
            ExpansionCount += Result.ExpansionCount;
            for Values in Result.StatePath.windows(2) {
                let Previous = Values[0].0;
                let Current = Values[1].0;
                if Tree.insert(Current) {
                    ParentByNode.insert(Current, Previous);
                }
                StateByNode.insert(Current, Values[1]);
            }
            for (PositionValue, Facing) in Result.RepeaterReservations {
                Repeaters.entry(PositionValue).or_insert(Facing);
            }
        }

        let mut TargetPaths = Vec::new();
        let OrderedBranches = TargetBranches;
        for Branch in OrderedBranches {
            let PortalTarget = Branch[0];
            let ReservedNodes: HashSet<_> = Branch.iter().copied().collect();
            if !Tree.contains(&PortalTarget) {
                let Some(Result) =
                    RouteIntoTree(PortalTarget, &Branch, &ReservedNodes, &Tree, &StateByNode)
                else {
                    return Failure(
                        "NoPath",
                        "NoPathGeometry",
                        usize::from(EnforceSignalStrength),
                        0,
                        ExpansionCount,
                    );
                };
                if Result.Status != "Routed" {
                    return Failure(
                        "NoPath",
                        if Result.NoPathReason.is_empty() {
                            "NoPathGeometry"
                        } else {
                            &Result.NoPathReason
                        },
                        Result.RepeaterRejectedCount,
                        Result.RepeaterConstraintFailures,
                        ExpansionCount + Result.ExpansionCount,
                    );
                };
                ExpansionCount += Result.ExpansionCount;
                for Values in Result.StatePath.windows(2) {
                    let Previous = Values[0].0;
                    let Current = Values[1].0;
                    if Tree.insert(Current) {
                        ParentByNode.insert(Current, Previous);
                    }
                    StateByNode.insert(Current, Values[1]);
                }
                for (PositionValue, Facing) in Result.RepeaterReservations {
                    Repeaters.entry(PositionValue).or_insert(Facing);
                }
            }

            let mut CurrentState = *StateByNode.get(&PortalTarget).unwrap();
            for Next in Branch.into_iter().skip(1) {
                if Tree.contains(&Next) {
                    CurrentState = *StateByNode.get(&Next).unwrap();
                    continue;
                }
                let Direction = (
                    Next.0 - CurrentState.0 .0,
                    Next.1 - CurrentState.0 .1,
                    Next.2 - CurrentState.0 .2,
                );
                let CanPlaceRepeater = CurrentState.1 != StartDirection
                    && CurrentState.1 == Direction
                    && Direction.1 == 0
                    && CurrentState.2 <= crate::PathRouting::REPEATER_TURN_HEADROOM;
                let RemainingStrength = if !EnforceSignalStrength {
                    MAXIMUM_UNREFRESHED_DUST_LENGTH
                } else if CanPlaceRepeater {
                    let Some(Facing) = RepeaterFacing(CurrentState.0, Next) else {
                        return Failure("NoPath", "NoPathGeometry", 1, 0, ExpansionCount);
                    };
                    Repeaters.entry(CurrentState.0).or_insert(Facing);
                    MAXIMUM_UNREFRESHED_DUST_LENGTH - 1
                } else if CurrentState.2 > 1 {
                    CurrentState.2 - 1
                } else {
                    return Failure("NoPath", "NoRepeater", 1, 1, ExpansionCount);
                };
                let NextState = (Next, Direction, RemainingStrength);
                ParentByNode.insert(Next, CurrentState.0);
                Tree.insert(Next);
                StateByNode.insert(Next, NextState);
                CurrentState = NextState;
            }

            let Target = CurrentState.0;
            let mut Path = vec![Target];
            let mut Cursor = Target;
            while Cursor != Root {
                let Some(Previous) = ParentByNode.get(&Cursor).copied() else {
                    return Failure("NoPath", "NoPathGeometry", 0, 0, ExpansionCount);
                };
                Path.push(Previous);
                Cursor = Previous;
            }
            Path.reverse();
            TargetPaths.push((Target, Path));
        }
        TargetPaths.sort_by_key(|Value| Value.0);
        let mut RepeaterReservations: Vec<_> = Repeaters.into_iter().collect();
        RepeaterReservations.sort_by_key(|Value| Value.0);
        // Boundary diagnostics are proportional to the routed tree, not the
        // entire sparse ownership region.  Scanning every allowed node for
        // every net made pass zero scale as nets times region size.
        let mut FinalNodes: Vec<_> = Tree.into_iter().collect();
        FinalNodes.sort_unstable();
        let BoundaryFrontierNodes = FinalNodes
            .iter()
            .filter(|Value| {
                self.Adjacency
                    .get(Value)
                    .into_iter()
                    .flatten()
                    .any(|Neighbor| !AllowedNodes.contains(Neighbor))
            })
            .copied()
            .collect();
        RouteTreeSearchResult {
            Status: "Routed".to_string(),
            NoPathReason: String::new(),
            Nodes: FinalNodes,
            TargetPaths,
            BoundaryFrontierNodes,
            RepeaterReservations,
            ExpansionCount,
            RepeaterRejectedCount: 0,
            RepeaterConstraintFailureCount: 0,
            IsRouted: true,
            IsBudgetExpired: false,
        }
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
        CompletionMask,
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
}
