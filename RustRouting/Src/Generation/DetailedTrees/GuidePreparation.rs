use super::*;

impl RoutingContext {
    pub(in crate::Generation) fn PrepareDetailedRouteGuide(
        &self,
        AllowedNodeValues: &[Position],
        PreferredColumns: &[(i32, i32)],
        ExternalNodeCostValues: &[(Position, i32)],
        GuidePenalty: i32,
        Deadline: &RuntimeDeadline,
    ) -> Option<PreparedDetailedRouteGuide> {
        let AllowedNodes: HashSet<_> = AllowedNodeValues.iter().copied().collect();
        let mut BoundaryBlockedNodes = HashSet::new();
        for (Index, Value) in AllowedNodes.iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            for Neighbor in self.Adjacency.get(Value).into_iter().flatten() {
                if !AllowedNodes.contains(Neighbor) {
                    BoundaryBlockedNodes.insert(*Neighbor);
                }
            }
        }
        let mut NodeCosts: HashMap<_, _> = ExternalNodeCostValues
            .iter()
            .copied()
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
        Some(PreparedDetailedRouteGuide {
            AllowedNodes,
            AllowedColumns: HashSet::new(),
            UseColumnMembership: false,
            BoundaryBlockedNodes,
            NodeCosts,
            ColumnCosts: HashMap::new(),
            PreferredColumns: PreferredColumns.to_vec(),
            ExactHintNodes: HashSet::new(),
            CertifiedPaths: Vec::new(),
            CertifiedRepeaters: Vec::new(),
            GuidePenalty,
        })
    }

    pub(in crate::Generation) fn PrepareDetailedRouteGuideFromColumns(
        &self,
        AllowedColumnValues: &[(i32, i32)],
        PreferredColumns: &[(i32, i32)],
        PreferredRoutingY: i32,
        GuidePenalty: i32,
        Deadline: &RuntimeDeadline,
    ) -> Option<PreparedDetailedRouteGuide> {
        if Deadline.Check() {
            return None;
        }
        let AllowedColumns: HashSet<_> = AllowedColumnValues.iter().copied().collect();
        let ColumnCosts = if GuidePenalty > 0 && !PreferredColumns.is_empty() {
            AllowedColumns
                .iter()
                .map(|Column| {
                    let Distance = PreferredColumns
                        .iter()
                        .map(|Preferred| {
                            (Column.0 - Preferred.0).abs() + (Column.1 - Preferred.1).abs()
                        })
                        .min()
                        .unwrap_or(0);
                    (*Column, Distance * GuidePenalty)
                })
                .collect()
        } else {
            HashMap::new()
        };
        let NodeCosts = AllowedColumns
            .iter()
            .flat_map(|Column| self.NodesByColumn.get(Column).into_iter().flatten())
            .filter_map(|PositionValue| {
                let Distance = (PositionValue.1 - PreferredRoutingY).abs();
                (Distance > 0)
                    .then_some((*PositionValue, Distance.saturating_mul(GuidePenalty.max(1))))
            })
            .collect::<HashMap<_, _>>();
        Some(PreparedDetailedRouteGuide {
            AllowedNodes: HashSet::new(),
            AllowedColumns,
            UseColumnMembership: true,
            BoundaryBlockedNodes: HashSet::new(),
            NodeCosts,
            ColumnCosts,
            PreferredColumns: PreferredColumns.to_vec(),
            ExactHintNodes: HashSet::new(),
            CertifiedPaths: Vec::new(),
            CertifiedRepeaters: Vec::new(),
            GuidePenalty,
        })
    }

    pub(in crate::Generation) fn CertifyRouteFactorConnectivityWithDeadline(
        &self,
        AllowedColumns: Vec<(i32, i32)>,
        RequiredAllowedNodeValues: Vec<Position>,
        BlockedNodeValues: Vec<Position>,
        ConnectivityRequiredNodeValues: Vec<Position>,
        Start: Position,
        MaximumExpansionCount: usize,
        Deadline: &RuntimeDeadline,
    ) -> (bool, bool, usize) {
        let mut AllowedNodes = HashSet::new();
        for (Index, Column) in AllowedColumns.into_iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return (false, false, 0);
            }
            if let Some(Values) = self.NodesByColumn.get(&Column) {
                AllowedNodes.extend(Values.iter().copied());
            }
        }
        AllowedNodes.extend(RequiredAllowedNodeValues);
        let mut BlockedNodes = HashSet::with_capacity(BlockedNodeValues.len());
        for (Index, Value) in BlockedNodeValues.into_iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return (false, false, 0);
            }
            BlockedNodes.insert(Value);
        }
        let RequiredNodes: HashSet<_> = ConnectivityRequiredNodeValues.into_iter().collect();
        if !AllowedNodes.contains(&Start)
            || BlockedNodes.contains(&Start)
            || RequiredNodes.is_empty()
            || RequiredNodes
                .iter()
                .any(|Value| !AllowedNodes.contains(Value) || BlockedNodes.contains(Value))
        {
            return (true, false, 0);
        }
        let ExpansionLimit = MaximumExpansionCount.max(1);
        let mut Reached = HashSet::from([Start]);
        let mut RemainingRequired = RequiredNodes;
        RemainingRequired.remove(&Start);
        if RemainingRequired.is_empty() {
            return (true, true, 0);
        }
        let mut Pending = vec![Start];
        let mut ExpansionCount = 0usize;
        while let Some(Current) = Pending.pop() {
            if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return (false, false, ExpansionCount);
            }
            if ExpansionCount >= ExpansionLimit {
                return (false, false, ExpansionCount);
            }
            ExpansionCount += 1;
            for Neighbor in self.Adjacency.get(&Current).into_iter().flatten() {
                if AllowedNodes.contains(Neighbor)
                    && !BlockedNodes.contains(Neighbor)
                    && Reached.insert(*Neighbor)
                {
                    RemainingRequired.remove(Neighbor);
                    if RemainingRequired.is_empty() {
                        return (true, true, ExpansionCount);
                    }
                    Pending.push(*Neighbor);
                }
            }
        }
        if Deadline.Check() {
            return (false, false, ExpansionCount);
        }
        (true, RemainingRequired.is_empty(), ExpansionCount)
    }

    pub(crate) fn CertifyRouteFactorConnectivityNative(
        &self,
        AllowedColumns: Vec<(i32, i32)>,
        RequiredAllowedNodeValues: Vec<Position>,
        BlockedNodeValues: Vec<Position>,
        ConnectivityRequiredNodeValues: Vec<Position>,
        Start: Position,
        MaximumExpansionCount: usize,
        MaximumRuntimeMilliseconds: u64,
    ) -> (bool, bool, usize) {
        let Ok(Deadline) = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        else {
            return (false, false, 0);
        };
        self.CertifyRouteFactorConnectivityWithDeadline(
            AllowedColumns,
            RequiredAllowedNodeValues,
            BlockedNodeValues,
            ConnectivityRequiredNodeValues,
            Start,
            MaximumExpansionCount,
            &Deadline,
        )
    }

    pub(crate) fn CertifyRouteFactorConnectivityBatchNative(
        &self,
        Requests: Vec<(
            Vec<(i32, i32)>,
            i32,
            Vec<Position>,
            Vec<Position>,
            Vec<Position>,
            Position,
            usize,
        )>,
        MaximumRuntimeMilliseconds: u64,
    ) -> Vec<(bool, bool, usize)> {
        let Ok(Deadline) = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        else {
            return vec![(false, false, 0); Requests.len()];
        };
        let RequestCount = Requests.len();
        let mut IndexedPositions: Vec<Position> = self.Adjacency.keys().copied().collect();
        IndexedPositions.sort_unstable();
        let PositionIndex: HashMap<Position, usize> = IndexedPositions
            .iter()
            .copied()
            .enumerate()
            .map(|(Index, PositionValue)| (PositionValue, Index))
            .collect();
        let IndexedAdjacency: Vec<Vec<usize>> = IndexedPositions
            .iter()
            .map(|PositionValue| {
                self.Adjacency
                    .get(PositionValue)
                    .into_iter()
                    .flatten()
                    .filter_map(|Neighbor| PositionIndex.get(Neighbor).copied())
                    .collect()
            })
            .collect();
        let IndexedNodesByColumn: HashMap<(i32, i32), Vec<usize>> = self
            .NodesByColumn
            .iter()
            .map(|(Column, Positions)| {
                (
                    *Column,
                    Positions
                        .iter()
                        .filter_map(|PositionValue| PositionIndex.get(PositionValue).copied())
                        .collect(),
                )
            })
            .collect();
        if Deadline.Check() {
            return vec![(false, false, 0); RequestCount];
        }
        RoutingThreadPool().install(|| {
            Requests
                .into_par_iter()
                .map(
                    |(
                        GuideColumns,
                        GuideExpansion,
                        RequiredAllowedNodeValues,
                        BlockedNodeValues,
                        ConnectivityRequiredNodeValues,
                        Start,
                        MaximumExpansionCount,
                    )| {
                        if Deadline.Check() {
                            return (false, false, 0);
                        }
                        if GuideExpansion < 0 {
                            return (true, false, 0);
                        }
                        let mut AllowedNodes = vec![false; IndexedPositions.len()];
                        let mut PreparedColumnCount = 0usize;
                        for (GuideX, GuideZ) in GuideColumns {
                            for DeltaX in -GuideExpansion..=GuideExpansion {
                                for DeltaZ in -GuideExpansion..=GuideExpansion {
                                    if DeltaX.abs() + DeltaZ.abs() <= GuideExpansion {
                                        if PreparedColumnCount % DEADLINE_CHECK_INTERVAL == 0
                                            && Deadline.Check()
                                        {
                                            return (false, false, 0);
                                        }
                                        if let Some(Indices) = IndexedNodesByColumn
                                            .get(&(GuideX + DeltaX, GuideZ + DeltaZ))
                                        {
                                            for Index in Indices {
                                                AllowedNodes[*Index] = true;
                                            }
                                        }
                                        PreparedColumnCount += 1;
                                    }
                                }
                            }
                        }
                        for PositionValue in RequiredAllowedNodeValues {
                            if let Some(Index) = PositionIndex.get(&PositionValue) {
                                AllowedNodes[*Index] = true;
                            }
                        }
                        let mut BlockedNodes = vec![false; IndexedPositions.len()];
                        for PositionValue in BlockedNodeValues {
                            if let Some(Index) = PositionIndex.get(&PositionValue) {
                                BlockedNodes[*Index] = true;
                            }
                        }
                        let Some(StartIndex) = PositionIndex.get(&Start).copied() else {
                            return (true, false, 0);
                        };
                        let mut RequiredNodes = vec![false; IndexedPositions.len()];
                        let mut RemainingRequiredCount = 0usize;
                        for PositionValue in ConnectivityRequiredNodeValues {
                            let Some(Index) = PositionIndex.get(&PositionValue).copied() else {
                                return (true, false, 0);
                            };
                            if !RequiredNodes[Index] {
                                RequiredNodes[Index] = true;
                                RemainingRequiredCount += 1;
                            }
                        }
                        if !AllowedNodes[StartIndex]
                            || BlockedNodes[StartIndex]
                            || RemainingRequiredCount == 0
                            || RequiredNodes.iter().enumerate().any(|(Index, Required)| {
                                *Required && (!AllowedNodes[Index] || BlockedNodes[Index])
                            })
                        {
                            return (true, false, 0);
                        }
                        if RequiredNodes[StartIndex] {
                            RequiredNodes[StartIndex] = false;
                            RemainingRequiredCount -= 1;
                        }
                        if RemainingRequiredCount == 0 {
                            return (true, true, 0);
                        }
                        let ExpansionLimit = MaximumExpansionCount.max(1);
                        let mut Reached = vec![false; IndexedPositions.len()];
                        Reached[StartIndex] = true;
                        let mut Pending = vec![StartIndex];
                        let mut ExpansionCount = 0usize;
                        while let Some(CurrentIndex) = Pending.pop() {
                            if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                                return (false, false, ExpansionCount);
                            }
                            if ExpansionCount >= ExpansionLimit {
                                return (false, false, ExpansionCount);
                            }
                            ExpansionCount += 1;
                            for NeighborIndex in &IndexedAdjacency[CurrentIndex] {
                                if AllowedNodes[*NeighborIndex]
                                    && !BlockedNodes[*NeighborIndex]
                                    && !Reached[*NeighborIndex]
                                {
                                    Reached[*NeighborIndex] = true;
                                    if RequiredNodes[*NeighborIndex] {
                                        RequiredNodes[*NeighborIndex] = false;
                                        RemainingRequiredCount -= 1;
                                        if RemainingRequiredCount == 0 {
                                            return (true, true, ExpansionCount);
                                        }
                                    }
                                    Pending.push(*NeighborIndex);
                                }
                            }
                        }
                        if Deadline.Check() {
                            return (false, false, ExpansionCount);
                        }
                        (true, RemainingRequiredCount == 0, ExpansionCount)
                    },
                )
                .collect()
        })
    }
}
