use super::*;

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

        let mut RemainingBranches = TargetBranches.to_vec();
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
}
