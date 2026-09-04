use super::*;

#[allow(dead_code)]
pub(in crate::Escape) struct LayeredAccessClaimOccupancy {
    pub(in crate::Escape) Wire: HashMap<Position, usize>,
    pub(in crate::Escape) Support: HashMap<Position, usize>,
    pub(in crate::Escape) Air: HashMap<Position, usize>,
}

#[allow(dead_code)]
impl LayeredAccessClaimOccupancy {
    pub(in crate::Escape) fn New(GuideClaims: &DeferredAccessCandidateValue) -> Self {
        let Count = |Values: &[Position]| {
            Values
                .iter()
                .copied()
                .map(|PositionValue| (PositionValue, 1usize))
                .collect::<HashMap<_, _>>()
        };
        Self {
            Wire: Count(&GuideClaims.Wire),
            Support: Count(&GuideClaims.Support),
            Air: Count(&GuideClaims.Air),
        }
    }

    pub(in crate::Escape) fn Conflicts(&self, Value: &DeferredAccessCandidateValue) -> bool {
        if Value.Support.iter().any(|PositionValue| {
            self.Wire.contains_key(PositionValue) || self.Air.contains_key(PositionValue)
        }) || Value.Wire.iter().any(|PositionValue| {
            self.Support.contains_key(PositionValue) || self.Air.contains_key(PositionValue)
        }) || Value.Air.iter().any(|PositionValue| {
            self.Wire.contains_key(PositionValue) || self.Support.contains_key(PositionValue)
        }) {
            return true;
        }
        false
    }

    pub(in crate::Escape) fn Add(&mut self, Value: &DeferredAccessCandidateValue) {
        for (Counts, Positions) in [
            (&mut self.Wire, &Value.Wire),
            (&mut self.Support, &Value.Support),
            (&mut self.Air, &Value.Air),
        ] {
            for PositionValue in Positions {
                *Counts.entry(*PositionValue).or_default() += 1;
            }
        }
    }

    pub(in crate::Escape) fn Remove(&mut self, Value: &DeferredAccessCandidateValue) {
        for (Counts, Positions) in [
            (&mut self.Wire, &Value.Wire),
            (&mut self.Support, &Value.Support),
            (&mut self.Air, &Value.Air),
        ] {
            for PositionValue in Positions {
                let Count = Counts
                    .get_mut(PositionValue)
                    .expect("selected layered access claim is occupied");
                *Count -= 1;
                if *Count == 0 {
                    Counts.remove(PositionValue);
                }
            }
        }
    }
}

#[allow(dead_code)]
pub(in crate::Escape) struct LayeredWireForestCheckpoint {
    pub(in crate::Escape) ExistingPositions: Vec<Position>,
    pub(in crate::Escape) NewPositions: Vec<Position>,
    pub(in crate::Escape) UnionChanges: Vec<(Position, Position, usize)>,
    pub(in crate::Escape) ComponentCount: usize,
    pub(in crate::Escape) CycleCount: usize,
}

#[allow(dead_code)]
pub(in crate::Escape) struct LayeredWireForestOccupancy {
    pub(in crate::Escape) ActiveCounts: HashMap<Position, usize>,
    pub(in crate::Escape) ParentByPosition: HashMap<Position, Position>,
    pub(in crate::Escape) SizeByRoot: HashMap<Position, usize>,
    pub(in crate::Escape) ComponentCount: usize,
    pub(in crate::Escape) CycleCount: usize,
}

#[allow(dead_code)]
impl LayeredWireForestOccupancy {
    pub(in crate::Escape) fn New(_GraphAdjacency: &HashMap<Position, Vec<Position>>) -> Self {
        Self {
            ActiveCounts: HashMap::new(),
            ParentByPosition: HashMap::new(),
            SizeByRoot: HashMap::new(),
            ComponentCount: 0,
            CycleCount: 0,
        }
    }

    pub(in crate::Escape) fn FindRoot(&self, mut PositionValue: Position) -> Position {
        loop {
            let Parent = self.ParentByPosition[&PositionValue];
            if Parent == PositionValue {
                return PositionValue;
            }
            PositionValue = Parent;
        }
    }

    pub(in crate::Escape) fn Add(&mut self, Positions: &[Position]) -> LayeredWireForestCheckpoint {
        let mut Checkpoint = LayeredWireForestCheckpoint {
            ExistingPositions: Vec::new(),
            NewPositions: Vec::new(),
            UnionChanges: Vec::new(),
            ComponentCount: self.ComponentCount,
            CycleCount: self.CycleCount,
        };
        for PositionValue in Positions {
            if let Some(Count) = self.ActiveCounts.get_mut(PositionValue) {
                *Count += 1;
                Checkpoint.ExistingPositions.push(*PositionValue);
                continue;
            }
            self.ActiveCounts.insert(*PositionValue, 1);
            self.ParentByPosition.insert(*PositionValue, *PositionValue);
            self.SizeByRoot.insert(*PositionValue, 1);
            self.ComponentCount += 1;
            Checkpoint.NewPositions.push(*PositionValue);
            for Neighbor in AccessNeighborPositions(*PositionValue)
                .into_iter()
                .filter(|Neighbor| self.ActiveCounts.contains_key(Neighbor))
            {
                let mut FirstRoot = self.FindRoot(*PositionValue);
                let mut SecondRoot = self.FindRoot(Neighbor);
                if FirstRoot == SecondRoot {
                    self.CycleCount += 1;
                    continue;
                }
                if self.SizeByRoot[&FirstRoot] < self.SizeByRoot[&SecondRoot] {
                    std::mem::swap(&mut FirstRoot, &mut SecondRoot);
                }
                let FirstSize = self.SizeByRoot[&FirstRoot];
                self.ParentByPosition.insert(SecondRoot, FirstRoot);
                self.SizeByRoot
                    .insert(FirstRoot, FirstSize + self.SizeByRoot[&SecondRoot]);
                self.ComponentCount -= 1;
                Checkpoint
                    .UnionChanges
                    .push((SecondRoot, FirstRoot, FirstSize));
            }
        }
        Checkpoint
    }

    pub(in crate::Escape) fn Restore(&mut self, Checkpoint: LayeredWireForestCheckpoint) {
        for (ChildRoot, ParentRoot, PreviousParentSize) in Checkpoint.UnionChanges.into_iter().rev()
        {
            self.ParentByPosition.insert(ChildRoot, ChildRoot);
            self.SizeByRoot.insert(ParentRoot, PreviousParentSize);
        }
        for PositionValue in Checkpoint.NewPositions.into_iter().rev() {
            self.ActiveCounts.remove(&PositionValue);
            self.ParentByPosition.remove(&PositionValue);
            self.SizeByRoot.remove(&PositionValue);
        }
        for PositionValue in Checkpoint.ExistingPositions {
            let Count = self
                .ActiveCounts
                .get_mut(&PositionValue)
                .expect("existing layered wire remains active");
            *Count -= 1;
        }
        self.ComponentCount = Checkpoint.ComponentCount;
        self.CycleCount = Checkpoint.CycleCount;
    }

    pub(in crate::Escape) fn IsCompleteConnectedBundle(
        &self,
        TerminalVariables: &[String],
    ) -> bool {
        self.ComponentCount == 1
            && TerminalVariables.iter().all(|Variable| {
                LayeredAccessTerminalVariablePosition(Variable)
                    .is_some_and(|PositionValue| self.ActiveCounts.contains_key(&PositionValue))
            })
    }
}

pub(in crate::Escape) struct LayeredPoweredWitnessWorkspace {
    pub(in crate::Escape) WireMask: Vec<bool>,
    pub(in crate::Escape) TargetMask: Vec<bool>,
    pub(in crate::Escape) PowerMaskByState: Vec<u16>,
    pub(in crate::Escape) TouchedWireIndices: Vec<usize>,
    pub(in crate::Escape) TouchedTargetIndices: Vec<usize>,
    pub(in crate::Escape) TouchedStateIndices: Vec<usize>,
}

impl LayeredPoweredWitnessWorkspace {
    pub(in crate::Escape) fn New(IndexedGraph: &IndexedEscapeGraph) -> Self {
        Self {
            WireMask: vec![false; IndexedGraph.Positions.len()],
            TargetMask: vec![false; IndexedGraph.Positions.len()],
            PowerMaskByState: vec![0; IndexedGraph.Positions.len() * ESCAPE_DIRECTION_STATE_COUNT],
            TouchedWireIndices: Vec::new(),
            TouchedTargetIndices: Vec::new(),
            TouchedStateIndices: Vec::new(),
        }
    }

    pub(in crate::Escape) fn Reset(&mut self) {
        for Index in self.TouchedWireIndices.drain(..) {
            self.WireMask[Index] = false;
        }
        for Index in self.TouchedTargetIndices.drain(..) {
            self.TargetMask[Index] = false;
        }
        for Index in self.TouchedStateIndices.drain(..) {
            self.PowerMaskByState[Index] = 0;
        }
    }

    pub(in crate::Escape) fn AddWirePosition(
        &mut self,
        IndexedGraph: &IndexedEscapeGraph,
        PositionValue: Position,
    ) -> bool {
        let Some(Index) = IndexedGraph.PositionIndices.get(&PositionValue).copied() else {
            return false;
        };
        if !self.WireMask[Index] {
            self.WireMask[Index] = true;
            self.TouchedWireIndices.push(Index);
        }
        true
    }

    pub(in crate::Escape) fn AddTargetIndex(&mut self, Index: usize) -> bool {
        if self.TargetMask[Index] {
            return false;
        }
        self.TargetMask[Index] = true;
        self.TouchedTargetIndices.push(Index);
        true
    }

    pub(in crate::Escape) fn RecordStatePower(&mut self, StateIndex: usize, Power: u8) -> bool {
        if !(1..=15).contains(&Power) {
            return false;
        }
        let ExistingMask = self.PowerMaskByState[StateIndex];
        let PowerBit = 1u16 << Power;
        let Dominated = if Power == 14 || Power == 15 {
            ExistingMask & PowerBit != 0
        } else {
            (Power..=13).any(|CandidatePower| ExistingMask & (1u16 << CandidatePower) != 0)
        };
        if Dominated {
            return false;
        }
        if ExistingMask == 0 {
            self.TouchedStateIndices.push(StateIndex);
        }
        self.PowerMaskByState[StateIndex] |= PowerBit;
        true
    }
}

pub(in crate::Escape) fn LayeredPoweredWitnessHasSelfExcitingCycle(
    IndexedGraph: &IndexedEscapeGraph,
    WitnessNodeIndices: &HashSet<usize>,
    RepeaterEndpoints: &HashMap<usize, (usize, usize)>,
    Deadline: &RuntimeDeadline,
) -> Option<bool> {
    if RepeaterEndpoints.is_empty() {
        return Some(false);
    }
    let mut OrderedNodeIndices = WitnessNodeIndices.iter().copied().collect::<Vec<_>>();
    OrderedNodeIndices.sort_unstable();
    let LocalIndexByNode = OrderedNodeIndices
        .iter()
        .enumerate()
        .map(|(LocalIndex, NodeIndex)| (*NodeIndex, LocalIndex))
        .collect::<HashMap<_, _>>();
    let mut Adjacency = vec![Vec::<usize>::new(); OrderedNodeIndices.len()];
    let mut ReverseAdjacency = vec![Vec::<usize>::new(); OrderedNodeIndices.len()];
    let mut WorkCount = 0usize;
    for (LocalIndex, CurrentNode) in OrderedNodeIndices.iter().copied().enumerate() {
        if WorkCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return None;
        }
        WorkCount = WorkCount.saturating_add(1);
        let Current = IndexedGraph.Positions[CurrentNode];
        let CandidateNodes = if let Some((_InputNode, OutputNode)) =
            RepeaterEndpoints.get(&CurrentNode)
        {
            vec![*OutputNode]
        } else {
            let (X, Y, Z) = Current;
            [
                (X + 1, Y, Z),
                (X - 1, Y, Z),
                (X, Y, Z + 1),
                (X, Y, Z - 1),
                (X + 1, Y + 1, Z),
                (X + 1, Y - 1, Z),
                (X - 1, Y + 1, Z),
                (X - 1, Y - 1, Z),
                (X, Y + 1, Z + 1),
                (X, Y - 1, Z + 1),
                (X, Y + 1, Z - 1),
                (X, Y - 1, Z - 1),
            ]
            .into_iter()
            .filter_map(|PositionValue| IndexedGraph.PositionIndices.get(&PositionValue).copied())
            .filter(|NeighborNode| {
                WitnessNodeIndices.contains(NeighborNode)
                    && RepeaterEndpoints
                        .get(NeighborNode)
                        .is_none_or(|(InputNode, _OutputNode)| *InputNode == CurrentNode)
            })
            .collect::<Vec<_>>()
        };
        for CandidateNode in CandidateNodes {
            let Some(NeighborLocalIndex) = LocalIndexByNode.get(&CandidateNode).copied() else {
                continue;
            };
            Adjacency[LocalIndex].push(NeighborLocalIndex);
            ReverseAdjacency[NeighborLocalIndex].push(LocalIndex);
        }
        Adjacency[LocalIndex].sort_unstable();
        Adjacency[LocalIndex].dedup();
    }

    // One strongly-connected-components pass replaces a separate physical
    // reachability search for every repeater.  A repeater is self-exciting
    // exactly when its input, body, and output lie in the same directed SCC.
    let mut Visited = vec![false; OrderedNodeIndices.len()];
    let mut FinishOrder = Vec::with_capacity(OrderedNodeIndices.len());
    for Start in 0..OrderedNodeIndices.len() {
        if Visited[Start] {
            continue;
        }
        Visited[Start] = true;
        let mut Pending = vec![(Start, 0usize)];
        while let Some((Current, NextIndex)) = Pending.last_mut() {
            if WorkCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            WorkCount = WorkCount.saturating_add(1);
            if *NextIndex < Adjacency[*Current].len() {
                let Neighbor = Adjacency[*Current][*NextIndex];
                *NextIndex += 1;
                if !Visited[Neighbor] {
                    Visited[Neighbor] = true;
                    Pending.push((Neighbor, 0));
                }
            } else {
                let (Finished, _NextIndex) = Pending.pop().expect("pending DFS is non-empty");
                FinishOrder.push(Finished);
            }
        }
    }
    let mut ComponentByLocalIndex = vec![usize::MAX; OrderedNodeIndices.len()];
    let mut ComponentIndex = 0usize;
    for Start in FinishOrder.into_iter().rev() {
        if ComponentByLocalIndex[Start] != usize::MAX {
            continue;
        }
        ComponentByLocalIndex[Start] = ComponentIndex;
        let mut Pending = vec![Start];
        while let Some(Current) = Pending.pop() {
            if WorkCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            WorkCount = WorkCount.saturating_add(1);
            for Neighbor in ReverseAdjacency[Current].iter().copied() {
                if ComponentByLocalIndex[Neighbor] == usize::MAX {
                    ComponentByLocalIndex[Neighbor] = ComponentIndex;
                    Pending.push(Neighbor);
                }
            }
        }
        ComponentIndex = ComponentIndex.saturating_add(1);
    }
    for (RepeaterNode, (InputNode, OutputNode)) in RepeaterEndpoints {
        let Some(RepeaterLocalIndex) = LocalIndexByNode.get(RepeaterNode).copied() else {
            continue;
        };
        let Some(InputLocalIndex) = LocalIndexByNode.get(InputNode).copied() else {
            continue;
        };
        let Some(OutputLocalIndex) = LocalIndexByNode.get(OutputNode).copied() else {
            continue;
        };
        let Component = ComponentByLocalIndex[RepeaterLocalIndex];
        if ComponentByLocalIndex[InputLocalIndex] == Component
            && ComponentByLocalIndex[OutputLocalIndex] == Component
        {
            return Some(true);
        }
    }
    Some(false)
}

pub(in crate::Escape) fn LayeredGuideAccessBundleHasPoweredTreeWitness(
    IndexedGraph: &IndexedEscapeGraph,
    Workspace: &mut LayeredPoweredWitnessWorkspace,
    GuideClaims: &DeferredAccessCandidateValue,
    DetailedHintPaths: &[Vec<Position>],
    TerminalVariables: &[String],
    SelectedByDomain: &[&DeferredAccessCandidateValue],
    DetachedSeedAccessPaths: &[Vec<Position>],
    SourceTerminalVariable: Option<&str>,
    SourceDetachedAnchorIndex: Option<usize>,
    Deadline: &RuntimeDeadline,
) -> Option<Option<(Vec<Vec<Position>>, Vec<(Position, String)>)>> {
    Workspace.Reset();
    if !GuideClaims
        .OrderedWire
        .iter()
        .copied()
        .chain(
            SelectedByDomain
                .iter()
                .flat_map(|Candidate| Candidate.OrderedWire.iter().copied()),
        )
        .chain(DetachedSeedAccessPaths.iter().flatten().copied())
        .chain(DetailedHintPaths.iter().flatten().copied())
        .all(|PositionValue| Workspace.AddWirePosition(IndexedGraph, PositionValue))
    {
        return Some(None);
    }
    let RootDomainIndex = SourceTerminalVariable.and_then(|SourceVariable| {
        TerminalVariables
            .iter()
            .position(|Variable| Variable == SourceVariable)
    });
    let mut SourcePaths = Vec::<&[Position]>::new();
    let mut TargetPaths = Vec::<&[Position]>::new();
    if let Some(RootDomainIndex) = RootDomainIndex {
        SourcePaths.push(SelectedByDomain[RootDomainIndex].OrderedWire.as_slice());
        TargetPaths.extend(
            SelectedByDomain
                .iter()
                .enumerate()
                .filter(|(DomainIndex, _Candidate)| *DomainIndex != RootDomainIndex)
                .map(|(_DomainIndex, Candidate)| Candidate.OrderedWire.as_slice()),
        );
        TargetPaths.extend(DetachedSeedAccessPaths.iter().map(Vec::as_slice));
    } else if let Some(SourceDetachedAnchorIndex) = SourceDetachedAnchorIndex {
        let Some(SourcePath) = DetachedSeedAccessPaths.get(SourceDetachedAnchorIndex) else {
            return Some(None);
        };
        SourcePaths.push(SourcePath.as_slice());
        TargetPaths.extend(
            SelectedByDomain
                .iter()
                .map(|Candidate| Candidate.OrderedWire.as_slice()),
        );
        TargetPaths.extend(
            DetachedSeedAccessPaths
                .iter()
                .enumerate()
                .filter(|(Index, _Path)| *Index != SourceDetachedAnchorIndex)
                .map(|(_Index, Path)| Path.as_slice()),
        );
    }
    if SourcePaths.is_empty() || TargetPaths.is_empty() {
        return Some(Some((Vec::new(), Vec::new())));
    }
    let mut CanReachAllTargets = |SourcePath: &[Position], TargetPaths: &[&[Position]]| {
        let Some(Source) = SourcePath.first().copied() else {
            return Some(None);
        };
        let Some(SourceIndex) = IndexedGraph.PositionIndices.get(&Source).copied() else {
            return Some(None);
        };
        if !Workspace.WireMask[SourceIndex] {
            return Some(None);
        }
        for TargetPath in TargetPaths {
            let Some(TargetPosition) = TargetPath.first().copied() else {
                return Some(None);
            };
            let Some(TargetIndex) = IndexedGraph.PositionIndices.get(&TargetPosition).copied()
            else {
                return Some(None);
            };
            if !Workspace.WireMask[TargetIndex] || !Workspace.AddTargetIndex(TargetIndex) {
                return Some(None);
            }
        }
        let InitialStateIndex = IndexedGraph.StateIndex(SourceIndex, (0, 0, 0));
        let InitialPoweredStateIndex = PoweredEscapeStateIndex(SourceIndex, (0, 0, 0), 15);
        Workspace.RecordStatePower(InitialStateIndex, 15);
        let mut Pending = VecDeque::from([(SourceIndex, ESCAPE_INITIAL_DIRECTION_STATE, 15u8)]);
        let mut ParentByState = HashMap::<usize, usize>::new();
        let mut ReachedTargetStates = Vec::new();
        let mut ReachedTargetCount = 0usize;
        let mut WorkCount = 0usize;
        while let Some((CurrentIndex, PriorDirectionIndex, PowerRemaining)) = Pending.pop_front() {
            if WorkCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            WorkCount = WorkCount.saturating_add(1);
            if Workspace.TargetMask[CurrentIndex] {
                Workspace.TargetMask[CurrentIndex] = false;
                ReachedTargetCount += 1;
                ReachedTargetStates.push(
                    (CurrentIndex * ESCAPE_DIRECTION_STATE_COUNT + PriorDirectionIndex)
                        * ESCAPE_POWER_STATE_COUNT
                        + usize::from(PowerRemaining),
                );
                if ReachedTargetCount == Workspace.TouchedTargetIndices.len() {
                    break;
                }
            }
            let Current = IndexedGraph.Positions[CurrentIndex];
            for NextIndex in IndexedGraph.NeighborIndices[CurrentIndex].iter().copied() {
                if !Workspace.WireMask[NextIndex] {
                    continue;
                }
                let Next = IndexedGraph.Positions[NextIndex];
                let DirectionValue = (Next.0 - Current.0, Next.1 - Current.1, Next.2 - Current.2);
                if PriorDirectionIndex != ESCAPE_INITIAL_DIRECTION_STATE {
                    let ReverseDirection =
                        (-DirectionValue.0, -DirectionValue.1, -DirectionValue.2);
                    if EscapeDirectionStateIndex(ReverseDirection) == PriorDirectionIndex {
                        continue;
                    }
                }
                let DirectionIndex = EscapeDirectionStateIndex(DirectionValue);
                let State = IndexedGraph.StateIndex(NextIndex, DirectionValue);
                let PoweredParentState = (CurrentIndex * ESCAPE_DIRECTION_STATE_COUNT
                    + PriorDirectionIndex)
                    * ESCAPE_POWER_STATE_COUNT
                    + usize::from(PowerRemaining);
                let DustPower = PowerRemaining.saturating_sub(1);
                if DustPower > 0 && Workspace.RecordStatePower(State, DustPower) {
                    let PoweredState =
                        PoweredEscapeStateIndex(NextIndex, DirectionValue, DustPower);
                    ParentByState.insert(PoweredState, PoweredParentState);
                    Pending.push_back((NextIndex, DirectionIndex, DustPower));
                }
                let CanPlaceRepeater = PriorDirectionIndex != ESCAPE_INITIAL_DIRECTION_STATE
                    && PriorDirectionIndex == DirectionIndex
                    && (1..=4).contains(&DirectionIndex)
                    && PowerRemaining < 14;
                if CanPlaceRepeater && Workspace.RecordStatePower(State, 14) {
                    let PoweredState = PoweredEscapeStateIndex(NextIndex, DirectionValue, 14);
                    ParentByState.insert(PoweredState, PoweredParentState);
                    Pending.push_back((NextIndex, DirectionIndex, 14));
                }
            }
        }
        if ReachedTargetCount != Workspace.TouchedTargetIndices.len() {
            return Some(None);
        }

        // Reconstruct the deterministic witness rather than treating
        // directional reachability through a feedback loop as a valid
        // combinational route.  Access conductors are fixed; guide and
        // detailed-hint cells are advisory and become physical only when the
        // settled witness actually uses them.  A strength increase identifies
        // the repeater at the parent state, with its exact input and output
        // neighbors.
        let mut WitnessNodeIndices = SelectedByDomain
            .iter()
            .flat_map(|Candidate| Candidate.OrderedWire.iter())
            .chain(DetachedSeedAccessPaths.iter().flatten())
            .filter_map(|PositionValue| IndexedGraph.PositionIndices.get(PositionValue))
            .copied()
            .collect::<HashSet<_>>();
        let mut RepeaterEndpoints = HashMap::<usize, (usize, usize)>::new();
        let mut WitnessPaths = Vec::<Vec<Position>>::new();
        for TargetState in ReachedTargetStates {
            let mut CurrentState = TargetState;
            let mut ReconstructedStates = HashSet::new();
            let mut ReconstructedPath = Vec::<Position>::new();
            while CurrentState != InitialPoweredStateIndex {
                if !ReconstructedStates.insert(CurrentState) {
                    return Some(None);
                }
                if ReconstructedStates.len() % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
                let Some(ParentState) = ParentByState.get(&CurrentState).copied() else {
                    return Some(None);
                };
                let CurrentNode = PoweredEscapeStateNodeIndex(CurrentState);
                let ParentNode = PoweredEscapeStateNodeIndex(ParentState);
                WitnessNodeIndices.insert(CurrentNode);
                WitnessNodeIndices.insert(ParentNode);
                ReconstructedPath.push(IndexedGraph.Positions[CurrentNode]);
                let CurrentPower = (CurrentState % ESCAPE_POWER_STATE_COUNT) as u8;
                let ParentPower = (ParentState % ESCAPE_POWER_STATE_COUNT) as u8;
                if CurrentPower > ParentPower {
                    let Some(InputState) = ParentByState.get(&ParentState).copied() else {
                        return Some(None);
                    };
                    let InputNode = PoweredEscapeStateNodeIndex(InputState);
                    if RepeaterEndpoints
                        .insert(ParentNode, (InputNode, CurrentNode))
                        .is_some_and(|Existing| Existing != (InputNode, CurrentNode))
                    {
                        return Some(None);
                    }
                }
                CurrentState = ParentState;
            }
            ReconstructedPath.push(Source);
            ReconstructedPath.reverse();
            ReconstructedPath.dedup();
            WitnessPaths.push(ReconstructedPath);
        }
        let HasCycle = LayeredPoweredWitnessHasSelfExcitingCycle(
            IndexedGraph,
            &WitnessNodeIndices,
            &RepeaterEndpoints,
            Deadline,
        )?;
        if HasCycle {
            return Some(None);
        }
        let mut WitnessRepeaters = Vec::<(Position, String)>::new();
        for (RepeaterNode, (InputNode, OutputNode)) in RepeaterEndpoints {
            let Repeater = IndexedGraph.Positions[RepeaterNode];
            let Input = IndexedGraph.Positions[InputNode];
            let Output = IndexedGraph.Positions[OutputNode];
            let Delta = (
                Output.0 - Repeater.0,
                Output.1 - Repeater.1,
                Output.2 - Repeater.2,
            );
            if Input
                != (
                    Repeater.0 - Delta.0,
                    Repeater.1 - Delta.1,
                    Repeater.2 - Delta.2,
                )
            {
                return Some(None);
            }
            let Facing = match Delta {
                (1, 0, 0) => "west",
                (-1, 0, 0) => "east",
                (0, 0, 1) => "north",
                (0, 0, -1) => "south",
                _ => return Some(None),
            };
            WitnessRepeaters.push((Repeater, Facing.to_owned()));
        }
        WitnessRepeaters.sort_unstable();
        Some(Some((WitnessPaths, WitnessRepeaters)))
    };
    CanReachAllTargets(SourcePaths[0], &TargetPaths)
}

pub(in crate::Escape) fn LayeredGuideHasSelfLegalAccessBundle<'a>(
    GuideClaims: &DeferredAccessCandidateValue,
    FixedBaseValues: &[&DeferredAccessCandidateValue],
    TerminalVariables: &[String],
    PortalTuple: &[&DeferredAccessCandidateValue],
    Domains: &[Vec<&'a DeferredAccessCandidateValue>],
    PreferredRequirements: &[Vec<(String, String)>],
    Deadline: &RuntimeDeadline,
) -> Result<
    (
        Vec<Vec<(String, String)>>,
        BTreeSet<(String, String)>,
        usize,
    ),
    (),
> {
    if TerminalVariables.len() != PortalTuple.len() || Domains.len() != PortalTuple.len() {
        return Ok((Vec::new(), BTreeSet::new(), 0));
    }
    let MatchingDomains = Domains
        .iter()
        .zip(PortalTuple)
        .enumerate()
        .map(|(DomainIndex, (Domain, PortalValue))| {
            let mut Matching = Domain
                .iter()
                .copied()
                .filter(|Access| Access.Portal == PortalValue.Portal)
                .collect::<Vec<_>>();
            Matching.sort_by_key(|Access| {
                (
                    PreferredRequirements
                        .iter()
                        .position(|Requirements| {
                            Requirements.iter().any(|(Variable, CandidateId)| {
                                Variable == &TerminalVariables[DomainIndex]
                                    && CandidateId == &Access.CandidateId
                            })
                        })
                        .unwrap_or(usize::MAX),
                    Access.CandidateId.as_str(),
                )
            });
            Matching
        })
        .collect::<Vec<_>>();
    if MatchingDomains.iter().any(Vec::is_empty) {
        return Ok((Vec::new(), BTreeSet::new(), 0));
    }
    let MatchingDomains = MatchingDomains
        .into_iter()
        .map(|Domain| {
            Domain
                .into_iter()
                .filter(|Candidate| {
                    !DeferredAccessCandidatesConflict(GuideClaims, Candidate)
                        && FixedBaseValues.iter().all(|BaseValue| {
                            !DeferredAccessCandidatesConflict(BaseValue, Candidate)
                        })
                })
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    if MatchingDomains.iter().any(Vec::is_empty) {
        return Ok((Vec::new(), BTreeSet::new(), 0));
    }
    let mut DomainOrder = (0..MatchingDomains.len()).collect::<Vec<_>>();
    DomainOrder.sort_by_key(|DomainIndex| (MatchingDomains[*DomainIndex].len(), *DomainIndex));
    let AllChoices = MatchingDomains
        .iter()
        .enumerate()
        .flat_map(|(DomainIndex, Domain)| {
            Domain.iter().map(move |Candidate| {
                (
                    TerminalVariables[DomainIndex].clone(),
                    Candidate.CandidateId.clone(),
                )
            })
        })
        .collect::<BTreeSet<_>>();
    for BaseValue in FixedBaseValues {
        if DeferredAccessCandidatesConflict(GuideClaims, BaseValue) {
            return Ok((Vec::new(), BTreeSet::new(), 0));
        }
    }
    let mut Selected = Vec::<(usize, &'a DeferredAccessCandidateValue)>::new();
    let mut Witnesses = Vec::<Vec<(String, String)>>::new();
    let mut SupportedChoices = BTreeSet::<(String, String)>::new();
    let mut ExpansionCount = 0usize;
    fn Search<'a>(
        OrderIndex: usize,
        DomainOrder: &[usize],
        TerminalVariables: &[String],
        Domains: &[Vec<&'a DeferredAccessCandidateValue>],
        Selected: &mut Vec<(usize, &'a DeferredAccessCandidateValue)>,
        Witnesses: &mut Vec<Vec<(String, String)>>,
        AllChoices: &BTreeSet<(String, String)>,
        SupportedChoices: &mut BTreeSet<(String, String)>,
        GuideClaims: &DeferredAccessCandidateValue,
        Deadline: &RuntimeDeadline,
        ExpansionCount: &mut usize,
    ) -> Result<(), ()> {
        if Deadline.Check() {
            return Err(());
        }
        *ExpansionCount = ExpansionCount.saturating_add(1);
        let Some(DomainIndex) = DomainOrder.get(OrderIndex).copied() else {
            let SelectedByDomain = (0..TerminalVariables.len())
                .map(|DomainIndex| {
                    Selected
                        .iter()
                        .find(|(SelectedDomainIndex, _Candidate)| {
                            *SelectedDomainIndex == DomainIndex
                        })
                        .map(|(_SelectedDomainIndex, Candidate)| *Candidate)
                        .expect("complete layered guide witness owns every terminal")
                })
                .collect::<Vec<_>>();
            let Requirements = SelectedByDomain
                .iter()
                .enumerate()
                .map(|(DomainIndex, Candidate)| {
                    (
                        TerminalVariables[DomainIndex].clone(),
                        Candidate.CandidateId.clone(),
                    )
                })
                .collect::<Vec<_>>();
            if SupportedChoices != AllChoices {
                SupportedChoices.extend(Requirements.iter().cloned());
            }
            Witnesses.push(Requirements);
            return Ok(());
        };
        for Candidate in &Domains[DomainIndex] {
            if Selected.iter().any(|(PreviousIndex, Previous)| {
                TerminalVariables[*PreviousIndex] == TerminalVariables[DomainIndex]
                    && Previous.CandidateId != Candidate.CandidateId
                    || DeferredAccessCandidatesConflict(Candidate, Previous)
            }) {
                continue;
            }
            Selected.push((DomainIndex, Candidate));
            Search(
                OrderIndex + 1,
                DomainOrder,
                TerminalVariables,
                Domains,
                Selected,
                Witnesses,
                AllChoices,
                SupportedChoices,
                GuideClaims,
                Deadline,
                ExpansionCount,
            )?;
            Selected.pop();
        }
        Ok(())
    }
    Search(
        0,
        &DomainOrder,
        TerminalVariables,
        &MatchingDomains,
        &mut Selected,
        &mut Witnesses,
        &AllChoices,
        &mut SupportedChoices,
        GuideClaims,
        Deadline,
        &mut ExpansionCount,
    )?;
    // Search order is already deterministic and reflects the physical access
    // preference supplied by the preceding portal tuple.  Sorting opaque stub
    // identifiers here discards that ordering and can bind an otherwise good
    // guide to a remote representative path.  Each complete assignment is
    // unique because every terminal variable is selected exactly once.
    Ok((Witnesses, SupportedChoices, ExpansionCount))
}
