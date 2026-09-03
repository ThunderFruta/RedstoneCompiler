//! Exact access-region graph and derived escape traversal.

use crate::Core::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Core::Models::{
    AssignmentCandidate, Direction, Position, TemplateRoutingAssignmentResult,
};
use crate::Core::Runtime::RoutingThreadPool;
use rayon::prelude::*;
use std::cmp::Reverse;
use std::collections::{BTreeMap, BTreeSet, BinaryHeap, HashMap, HashSet};
use std::sync::Arc;

use super::Candidates::ExactLayeredAccessPathCanCarryPower;
use super::State::*;

pub(super) fn BuildAccessRegionGraphFromRecipe(
    Recipe: &AccessRegionGraphRecipeValue,
    Deadline: &RuntimeDeadline,
) -> AccessRegionGraphResultValue {
    let (
        MemberId,
        (MinimumX, MaximumX, MinimumY, MaximumY, MinimumZ, MaximumZ),
        AllowedAccessValues,
        ActualBlockValues,
        ElectricalBlockValues,
        SolidBlockValues,
        TorchPoweredSupportValues,
        NeighborOffsets,
    ) = Recipe;
    let AllowedAccess = AllowedAccessValues.iter().copied().collect::<HashSet<_>>();
    let ActualBlocks = ActualBlockValues.iter().copied().collect::<HashSet<_>>();
    let ElectricalBlocks = ElectricalBlockValues
        .iter()
        .copied()
        .collect::<HashSet<_>>();
    let SolidBlocks = SolidBlockValues.iter().copied().collect::<HashSet<_>>();
    let TorchPoweredSupports = TorchPoweredSupportValues
        .iter()
        .copied()
        .collect::<HashSet<_>>();
    let mut StaticKeepOut = ElectricalBlocks
        .union(&SolidBlocks)
        .copied()
        .collect::<HashSet<_>>();
    for PositionValue in ElectricalBlocks.union(&SolidBlocks) {
        for (DeltaX, DeltaY, DeltaZ) in NeighborOffsets {
            StaticKeepOut.insert((
                PositionValue.0 + DeltaX,
                PositionValue.1 + DeltaY,
                PositionValue.2 + DeltaZ,
            ));
        }
    }
    let mut Nodes = Vec::new();
    let mut CheckedColumns = 0usize;
    for X in *MinimumX..=*MaximumX {
        for Z in *MinimumZ..=*MaximumZ {
            if CheckedColumns % 32 == 0 && Deadline.Check() {
                return (MemberId.clone(), Vec::new(), 0, 0, false);
            }
            CheckedColumns += 1;
            for Y in *MinimumY..=*MaximumY {
                let PositionValue = (X, Y, Z);
                let Support = (X, Y - 1, Z);
                if TorchPoweredSupports.contains(&Support) {
                    continue;
                }
                let IsLegal = if AllowedAccess.contains(&PositionValue) {
                    !ActualBlocks.contains(&PositionValue)
                } else {
                    !ActualBlocks.contains(&PositionValue)
                        && !ActualBlocks.contains(&Support)
                        && !StaticKeepOut.contains(&PositionValue)
                };
                if IsLegal {
                    Nodes.push(PositionValue);
                }
            }
        }
    }
    Nodes.sort_unstable();
    Nodes.dedup();
    let NodeSet = Nodes.iter().copied().collect::<HashSet<_>>();
    let mut DirectedEdgeCount = 0usize;
    let mut Adjacency = Vec::with_capacity(Nodes.len());
    for (NodeIndex, PositionValue) in Nodes.iter().copied().enumerate() {
        if NodeIndex % 256 == 0 && Deadline.Check() {
            return (MemberId.clone(), Vec::new(), 0, 0, false);
        }
        let mut Neighbors = Vec::with_capacity(NeighborOffsets.len());
        for (DeltaX, DeltaY, DeltaZ) in NeighborOffsets {
            let Neighbor = (
                PositionValue.0 + DeltaX,
                PositionValue.1 + DeltaY,
                PositionValue.2 + DeltaZ,
            );
            if !NodeSet.contains(&Neighbor) {
                continue;
            }
            if PositionValue.1 != Neighbor.1 {
                let (Lower, Upper) = if PositionValue.1 < Neighbor.1 {
                    (PositionValue, Neighbor)
                } else {
                    (Neighbor, PositionValue)
                };
                let Headroom = (Lower.0, Lower.1 + 1, Lower.2);
                let UpperSupport = (Upper.0, Upper.1 - 1, Upper.2);
                if SolidBlocks.contains(&Headroom)
                    || ActualBlocks.contains(&Headroom)
                    || (ActualBlocks.contains(&UpperSupport)
                        && !SolidBlocks.contains(&UpperSupport))
                {
                    continue;
                }
            }
            Neighbors.push(Neighbor);
        }
        Neighbors.sort_unstable();
        Neighbors.dedup();
        DirectedEdgeCount = DirectedEdgeCount.saturating_add(Neighbors.len());
        Adjacency.push((PositionValue, Neighbors));
    }
    (
        MemberId.clone(),
        Adjacency,
        Nodes.len(),
        DirectedEdgeCount / 2,
        true,
    )
}

pub(crate) fn BuildAccessRegionGraphCatalogWithDeadline(
    Recipes: Vec<AccessRegionGraphRecipeValue>,
    Deadline: RuntimeDeadline,
) -> (Vec<AccessRegionGraphResultValue>, bool) {
    let Results = RoutingThreadPool().install(|| {
        Recipes
            .par_iter()
            .map(|Recipe| BuildAccessRegionGraphFromRecipe(Recipe, &Deadline))
            .collect::<Vec<_>>()
    });
    let Complete = Results.iter().all(|Value| Value.4) && !Deadline.WasExceeded();
    (Results, Complete)
}
pub(crate) type LayeredAccessGuideSignalValue = (
    String,
    Vec<String>,
    usize,
    Vec<(i32, i32)>,
    Option<String>,
    Option<usize>,
);
pub(crate) type LayeredAccessBaseClaimValue = (
    String,
    Vec<Position>,
    Vec<Position>,
    Vec<Position>,
    Vec<Position>,
);
pub(crate) type LayeredAccessGuideControlsValue = (
    Vec<i32>,
    i32,
    i32,
    usize,
    usize,
    usize,
    usize,
    usize,
    Vec<Position>,
    Vec<LayeredAccessGuideSignalValue>,
    Vec<LayeredAccessBaseClaimValue>,
    Vec<(String, Vec<Vec<Position>>)>,
);
pub(crate) type LayeredAccessGuideMemberValue = (
    String,
    Vec<i64>,
    usize,
    Vec<EscapeRequest>,
    Vec<(String, String, String)>,
    i32,
    usize,
    LayeredAccessGuideControlsValue,
);
pub(crate) type SelectedLayeredGuideValue = (
    String,
    String,
    Vec<(String, String)>,
    i32,
    String,
    i32,
    Vec<Position>,
    Vec<Vec<Position>>,
    Vec<Position>,
    Vec<Vec<Position>>,
    Vec<(Position, String)>,
);
pub(super) type PreparedLayeredAccessGuideDomain = (
    BTreeMap<String, Vec<AssignmentCandidate>>,
    usize,
    HashMap<String, SelectedLayeredGuideValue>,
    Arc<Vec<Vec<(usize, usize)>>>,
);
pub(crate) type LayeredAccessGuideSelectionResult = (
    TemplateRoutingAssignmentResult,
    Option<LayeredEscapeMemberResult>,
    Vec<SelectedLayeredGuideValue>,
    usize,
);
pub(crate) type LayeredAccessEscapeSelectionResult = (
    TemplateRoutingAssignmentResult,
    Option<LayeredEscapeMemberResult>,
    usize,
);

pub(super) fn BuildOneDerivedEscapeRequest(
    Adjacency: &HashMap<Position, Vec<Position>>,
    IndexedGraph: &IndexedEscapeGraph,
    Workspace: &mut IndexedEscapeWorkspace,
    Request: EscapeRequest,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
    MaximumY: Option<i32>,
    mut SharedExpansionLease: Option<&mut SharedEscapeExpansionLease<'_>>,
) -> (EscapeRequestResult, bool, bool) {
    let (
        RequestId,
        Start,
        OrderedIngresses,
        RejectedStates,
        FixedPrefix,
        AllowedNodes,
        FirstPathPerIngress,
    ) = Request;
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE_VERBOSE").is_some() {
        eprintln!(
            "native layered escape request={} start={:?} ingresses={:?} first_path_per_ingress={}",
            RequestId, Start, OrderedIngresses, FirstPathPerIngress,
        );
    }
    let Ingresses: HashSet<Position> = OrderedIngresses.iter().copied().collect();
    let RejectedStateSet: HashSet<(Position, Direction)> = RejectedStates.into_iter().collect();
    // An empty request mask denotes the member's complete immutable graph.
    // Layered access catalogs otherwise repeated the same large position
    // vector and HashSet construction for every terminal prefix.
    let AllowsAllNodes = AllowedNodes.is_empty();
    let AllowedNodeSet: HashSet<Position> = AllowedNodes.into_iter().collect();
    let AllowsNode = |PositionValue: &Position| {
        MaximumY.is_none_or(|Value| PositionValue.1 <= Value)
            && (AllowsAllNodes || AllowedNodeSet.contains(PositionValue))
    };
    let InitialDirection: Direction = (0, 0, 0);
    let mut RemainingIngressStates: HashSet<(Position, Direction)> = Ingresses
        .iter()
        .flat_map(|Ingress| {
            let mut States: Vec<(Position, Direction)> = Adjacency
                .get(Ingress)
                .into_iter()
                .flatten()
                .filter(|Neighbor| AllowsNode(Neighbor))
                .map(|Neighbor| {
                    (
                        *Ingress,
                        (
                            Ingress.0 - Neighbor.0,
                            Ingress.1 - Neighbor.1,
                            Ingress.2 - Neighbor.2,
                        ),
                    )
                })
                .collect();
            if *Ingress == Start {
                States.push((*Ingress, InitialDirection));
            }
            States
        })
        .filter(|State| !RejectedStateSet.contains(State))
        .collect();
    let mut RemainingIngresses = Ingresses.clone();
    let mut Candidates = Vec::new();
    if !Adjacency.contains_key(&Start)
        || !AllowsNode(&Start)
        || Ingresses
            .iter()
            .any(|Ingress| !Adjacency.contains_key(Ingress) || !AllowsNode(Ingress))
    {
        return ((RequestId, Candidates, 0, true), false, false);
    }

    let mut PrefixClaims: Option<Arc<EscapeClaimNode>> = None;
    let mut PriorPrefixPosition = None;
    for PositionValue in &FixedPrefix {
        PrefixClaims = ExtendEscapeClaims(PrefixClaims, PriorPrefixPosition, *PositionValue);
        if PrefixClaims.is_none() {
            return ((RequestId, Candidates, 0, true), false, false);
        }
        PriorPrefixPosition = Some(*PositionValue);
    }
    if PriorPrefixPosition != Some(Start) {
        PrefixClaims = ExtendEscapeClaims(PrefixClaims, PriorPrefixPosition, Start);
        if PrefixClaims.is_none() {
            return ((RequestId, Candidates, 0, true), false, false);
        }
    }
    let StartState = (Start, InitialDirection);
    let StartClaims = PrefixClaims.expect("escape start claim");
    let mut ExpansionCount = 0usize;
    let mut WorkCapExceeded = false;
    let mut DeadlineExceeded = false;

    if !FirstPathPerIngress {
        let AllowedNodeIndices = if AllowsAllNodes && MaximumY.is_none() {
            None
        } else {
            let mut Values = vec![false; IndexedGraph.Positions.len()];
            if AllowsAllNodes {
                for (Index, PositionValue) in IndexedGraph.Positions.iter().enumerate() {
                    Values[Index] = AllowsNode(PositionValue);
                }
            } else {
                for PositionValue in &AllowedNodeSet {
                    if AllowsNode(PositionValue) {
                        if let Some(Index) = IndexedGraph.PositionIndices.get(PositionValue) {
                            Values[*Index] = true;
                        }
                    }
                }
            }
            Some(Values)
        };
        let StartNodeIndex = *IndexedGraph
            .PositionIndices
            .get(&Start)
            .expect("validated escape start is indexed");
        let StartStateIndex = IndexedGraph.StateIndex(StartNodeIndex, InitialDirection);
        let mut IndexedPrefixClaims = Vec::with_capacity(FixedPrefix.len() + 1);
        let mut IndexedPrefixParent = None;
        let mut IndexedPriorPrefixPosition = None;
        for PositionValue in &FixedPrefix {
            let Claim = ExtendIndexedEscapeClaims(
                &IndexedPrefixClaims,
                IndexedPrefixParent,
                IndexedPriorPrefixPosition,
                *PositionValue,
            )
            .expect("Arc-validated escape prefix must remain self-legal");
            IndexedPrefixClaims.push(Claim);
            IndexedPrefixParent = Some(IndexedPrefixClaims.len() - 1);
            IndexedPriorPrefixPosition = Some(*PositionValue);
        }
        if IndexedPriorPrefixPosition != Some(Start) {
            let Claim = ExtendIndexedEscapeClaims(
                &IndexedPrefixClaims,
                IndexedPrefixParent,
                IndexedPriorPrefixPosition,
                Start,
            )
            .expect("Arc-validated escape start must remain self-legal");
            IndexedPrefixClaims.push(Claim);
            IndexedPrefixParent = Some(IndexedPrefixClaims.len() - 1);
        }
        let IndexedStartClaimRecord = IndexedPrefixParent.expect("escape start claim");
        Workspace.BeginSearch();
        Workspace
            .ClaimRecords
            .extend_from_slice(&IndexedPrefixClaims);
        Workspace.SetState(StartStateIndex, 0, usize::MAX, IndexedStartClaimRecord);
        let mut Frontier = BinaryHeap::from([Reverse((
            0usize,
            Start,
            InitialDirection,
            StartNodeIndex,
            StartStateIndex,
        ))]);
        while let Some(Reverse((
            Cost,
            Current,
            PriorDirection,
            CurrentNodeIndex,
            CurrentStateIndex,
        ))) = Frontier.pop()
        {
            if Workspace.Cost(CurrentStateIndex) != Cost {
                continue;
            }
            if SharedExpansionLease.is_none() && ExpansionCount >= MaximumExpansionCount {
                WorkCapExceeded = true;
                break;
            }
            if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                DeadlineExceeded = true;
                break;
            }
            if !TryCountEscapeExpansion(
                &mut ExpansionCount,
                MaximumExpansionCount,
                SharedExpansionLease.as_deref_mut(),
            ) {
                WorkCapExceeded = true;
                break;
            }
            let CurrentState = (Current, PriorDirection);
            if RemainingIngressStates.remove(&CurrentState) {
                let mut ReversePath = Vec::new();
                let mut Cursor = CurrentStateIndex;
                loop {
                    ReversePath.push(IndexedGraph.StatePosition(Cursor));
                    let ParentState = Workspace.ParentStates[Cursor];
                    if ParentState == usize::MAX {
                        break;
                    }
                    Cursor = ParentState;
                }
                ReversePath.reverse();
                Candidates.push((Current, PriorDirection, ReversePath));
                if RemainingIngressStates.is_empty() {
                    break;
                }
            }
            for NextNodeIndex in &IndexedGraph.NeighborIndices[CurrentNodeIndex] {
                if AllowedNodeIndices
                    .as_ref()
                    .is_some_and(|Values| !Values[*NextNodeIndex])
                {
                    continue;
                }
                let Next = IndexedGraph.Positions[*NextNodeIndex];
                let DirectionValue = (Next.0 - Current.0, Next.1 - Current.1, Next.2 - Current.2);
                let TurnCost =
                    if PriorDirection != InitialDirection && DirectionValue != PriorDirection {
                        BendPenalty
                    } else {
                        0
                    };
                let NextCost = Cost.saturating_add(1).saturating_add(TurnCost);
                let NextStateIndex = IndexedGraph.StateIndex(*NextNodeIndex, DirectionValue);
                if NextCost >= Workspace.Cost(NextStateIndex) {
                    continue;
                }
                let CurrentClaimRecord = Workspace.ClaimRecordByState[CurrentStateIndex];
                let Some(NextClaims) = ExtendIndexedEscapeClaims(
                    &Workspace.ClaimRecords,
                    Some(CurrentClaimRecord),
                    Some(Current),
                    Next,
                ) else {
                    continue;
                };
                Workspace.ClaimRecords.push(NextClaims);
                let NextClaimRecord = Workspace.ClaimRecords.len() - 1;
                Workspace.SetState(NextStateIndex, NextCost, CurrentStateIndex, NextClaimRecord);
                Frontier.push(Reverse((
                    NextCost,
                    Next,
                    DirectionValue,
                    *NextNodeIndex,
                    NextStateIndex,
                )));
            }
        }
        return (
            (
                RequestId,
                Candidates,
                ExpansionCount,
                !WorkCapExceeded && !DeadlineExceeded,
            ),
            WorkCapExceeded,
            DeadlineExceeded,
        );
    }

    if FirstPathPerIngress {
        // Large face-restricted access graphs need target-directed search:
        // flooding their full directional state space for every terminal is
        // representation work when the caller consumes one path per ingress.
        // Fixed-band callers request the complete shared traversal below so
        // their exact directional-state bound remains authoritative.
        let AllowedNodeIndices = if AllowsAllNodes && MaximumY.is_none() {
            None
        } else {
            let mut Values = vec![false; IndexedGraph.Positions.len()];
            if AllowsAllNodes {
                for (Index, PositionValue) in IndexedGraph.Positions.iter().enumerate() {
                    Values[Index] = AllowsNode(PositionValue);
                }
            } else {
                for PositionValue in &AllowedNodeSet {
                    if AllowsNode(PositionValue) {
                        if let Some(Index) = IndexedGraph.PositionIndices.get(PositionValue) {
                            Values[*Index] = true;
                        }
                    }
                }
            }
            Some(Values)
        };
        let StartNodeIndex = *IndexedGraph
            .PositionIndices
            .get(&Start)
            .expect("validated escape start is indexed");
        let StartStateIndex = IndexedGraph.StateIndex(StartNodeIndex, InitialDirection);
        let mut IndexedPrefixClaims = Vec::with_capacity(FixedPrefix.len() + 1);
        let mut IndexedPrefixParent = None;
        let mut IndexedPriorPrefixPosition = None;
        for PositionValue in &FixedPrefix {
            let Claim = ExtendIndexedEscapeClaims(
                &IndexedPrefixClaims,
                IndexedPrefixParent,
                IndexedPriorPrefixPosition,
                *PositionValue,
            )
            .expect("Arc-validated escape prefix must remain self-legal");
            IndexedPrefixClaims.push(Claim);
            IndexedPrefixParent = Some(IndexedPrefixClaims.len() - 1);
            IndexedPriorPrefixPosition = Some(*PositionValue);
        }
        if IndexedPriorPrefixPosition != Some(Start) {
            let Claim = ExtendIndexedEscapeClaims(
                &IndexedPrefixClaims,
                IndexedPrefixParent,
                IndexedPriorPrefixPosition,
                Start,
            )
            .expect("Arc-validated escape start must remain self-legal");
            IndexedPrefixClaims.push(Claim);
            IndexedPrefixParent = Some(IndexedPrefixClaims.len() - 1);
        }
        let IndexedStartClaimRecord = IndexedPrefixParent.expect("escape start claim");
        for Ingress in OrderedIngresses {
            Workspace.BeginSearch();
            Workspace
                .ClaimRecords
                .extend_from_slice(&IndexedPrefixClaims);
            Workspace.SetState(StartStateIndex, 0, usize::MAX, IndexedStartClaimRecord);
            let mut OriginalFrontier = BinaryHeap::from([Reverse((
                0usize,
                0usize,
                Start,
                InitialDirection,
                StartNodeIndex,
                StartStateIndex,
            ))]);
            let mut OriginalCandidates = Vec::new();
            let mut RemainingTargetStates = RemainingIngressStates.clone();
            while let Some(Reverse((
                _EstimatedCost,
                Cost,
                Current,
                PriorDirection,
                CurrentNodeIndex,
                CurrentStateIndex,
            ))) = OriginalFrontier.pop()
            {
                if Workspace.Cost(CurrentStateIndex) != Cost {
                    continue;
                }
                if SharedExpansionLease.is_none() && ExpansionCount >= MaximumExpansionCount {
                    WorkCapExceeded = true;
                    break;
                }
                if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    DeadlineExceeded = true;
                    break;
                }
                if !TryCountEscapeExpansion(
                    &mut ExpansionCount,
                    MaximumExpansionCount,
                    SharedExpansionLease.as_deref_mut(),
                ) {
                    WorkCapExceeded = true;
                    break;
                }
                if Current == Ingress
                    && !RejectedStateSet.contains(&(Current, PriorDirection))
                    && RemainingTargetStates.remove(&(Current, PriorDirection))
                {
                    let mut ReversePath = Vec::new();
                    let mut Cursor = CurrentStateIndex;
                    loop {
                        ReversePath.push(IndexedGraph.StatePosition(Cursor));
                        let ParentState = Workspace.ParentStates[Cursor];
                        if ParentState == usize::MAX {
                            break;
                        }
                        Cursor = ParentState;
                    }
                    ReversePath.reverse();
                    OriginalCandidates.push((Current, PriorDirection, ReversePath));
                    break;
                }
                for NextNodeIndex in &IndexedGraph.NeighborIndices[CurrentNodeIndex] {
                    if AllowedNodeIndices
                        .as_ref()
                        .is_some_and(|Values| !Values[*NextNodeIndex])
                    {
                        continue;
                    }
                    let Next = IndexedGraph.Positions[*NextNodeIndex];
                    let DirectionValue =
                        (Next.0 - Current.0, Next.1 - Current.1, Next.2 - Current.2);
                    let TurnCost =
                        if PriorDirection != InitialDirection && DirectionValue != PriorDirection {
                            BendPenalty
                        } else {
                            0
                        };
                    let NextCost = Cost.saturating_add(1).saturating_add(TurnCost);
                    let NextStateIndex = IndexedGraph.StateIndex(*NextNodeIndex, DirectionValue);
                    if NextCost >= Workspace.Cost(NextStateIndex) {
                        continue;
                    }
                    let CurrentClaimRecord = Workspace.ClaimRecordByState[CurrentStateIndex];
                    let Some(NextClaims) = ExtendIndexedEscapeClaims(
                        &Workspace.ClaimRecords,
                        Some(CurrentClaimRecord),
                        Some(Current),
                        Next,
                    ) else {
                        continue;
                    };
                    Workspace.ClaimRecords.push(NextClaims);
                    let NextClaimRecord = Workspace.ClaimRecords.len() - 1;
                    Workspace.SetState(
                        NextStateIndex,
                        NextCost,
                        CurrentStateIndex,
                        NextClaimRecord,
                    );
                    let Heuristic =
                        LayeredEscapeLowerBound(Next, DirectionValue, Ingress, BendPenalty);
                    OriginalFrontier.push(Reverse((
                        NextCost.saturating_add(Heuristic),
                        NextCost,
                        Next,
                        DirectionValue,
                        *NextNodeIndex,
                        NextStateIndex,
                    )));
                }
            }
            let OriginalIsPowerCertified =
                OriginalCandidates
                    .iter()
                    .any(|(_Current, _Direction, Path)| {
                        let WirePath = FixedPrefix
                            .iter()
                            .copied()
                            .chain(Path.iter().copied().skip(1))
                            .collect::<Vec<_>>();
                        ExactLayeredAccessPathCanCarryPower(true, &WirePath)
                            && ExactLayeredAccessPathCanCarryPower(false, &WirePath)
                    });
            let OriginalAlternativeBlockedNode =
                OriginalCandidates
                    .first()
                    .and_then(|(_Current, _Direction, Path)| {
                        Path.iter()
                            .copied()
                            .skip(1)
                            .take(Path.len().saturating_sub(2))
                            .nth(Path.len().saturating_sub(2) / 2)
                    });
            Candidates.extend(OriginalCandidates);
            if WorkCapExceeded || DeadlineExceeded {
                break;
            }
            let mut AlternativeBlockedNodes = BTreeSet::<Position>::new();
            AlternativeBlockedNodes.extend(OriginalAlternativeBlockedNode);
            let AlternativeCount = if OriginalIsPowerCertified { 1 } else { 3 };
            let Visits = &mut Workspace.PoweredVisits;
            for AlternativeIndex in 0..AlternativeCount {
                let StartPoweredState =
                    PoweredEscapeStateIndex(StartNodeIndex, InitialDirection, 15);
                Visits.clear();
                Visits.insert(
                    StartPoweredState,
                    PoweredEscapeVisit::New(0, usize::MAX, IndexedStartClaimRecord),
                );
                let mut ClaimRecords = IndexedPrefixClaims.clone();
                let mut Frontier = BinaryHeap::from([Reverse((
                    0usize,
                    0usize,
                    Start,
                    InitialDirection,
                    0u8,
                    StartNodeIndex,
                    15u8,
                ))]);
                let mut PoweredCandidateFound = false;
                while let Some(Reverse((
                    _EstimatedCost,
                    Cost,
                    Current,
                    PriorDirection,
                    _PowerTieBreak,
                    CurrentNodeIndex,
                    PowerRemaining,
                ))) = Frontier.pop()
                {
                    let CurrentState =
                        PoweredEscapeStateIndex(CurrentNodeIndex, PriorDirection, PowerRemaining);
                    if Visits.get(&CurrentState).map(|Visit| Visit.CostValue()) != Some(Cost) {
                        continue;
                    }
                    if SharedExpansionLease.is_none() && ExpansionCount >= MaximumExpansionCount {
                        WorkCapExceeded = true;
                        break;
                    }
                    if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                        DeadlineExceeded = true;
                        break;
                    }
                    if !TryCountEscapeExpansion(
                        &mut ExpansionCount,
                        MaximumExpansionCount,
                        SharedExpansionLease.as_deref_mut(),
                    ) {
                        WorkCapExceeded = true;
                        break;
                    }
                    if Current == Ingress && !RejectedStateSet.contains(&(Current, PriorDirection))
                    {
                        let mut ReversePath = Vec::new();
                        let mut Cursor = CurrentState;
                        loop {
                            ReversePath
                                .push(IndexedGraph.Positions[PoweredEscapeStateNodeIndex(Cursor)]);
                            let ParentState = Visits[&Cursor].ParentStateValue();
                            if ParentState == usize::MAX {
                                break;
                            }
                            Cursor = ParentState;
                        }
                        ReversePath.reverse();
                        AlternativeBlockedNodes.extend(
                            ReversePath
                                .iter()
                                .copied()
                                .skip(1)
                                .take(ReversePath.len().saturating_sub(2))
                                .nth(ReversePath.len().saturating_sub(2) / 2),
                        );
                        Candidates.push((Current, PriorDirection, ReversePath));
                        PoweredCandidateFound = true;
                        break;
                    }
                    for NextNodeIndex in &IndexedGraph.NeighborIndices[CurrentNodeIndex] {
                        if AllowedNodeIndices
                            .as_ref()
                            .is_some_and(|Values| !Values[*NextNodeIndex])
                        {
                            continue;
                        }
                        let Next = IndexedGraph.Positions[*NextNodeIndex];
                        if (OriginalIsPowerCertified || AlternativeIndex > 0)
                            && AlternativeBlockedNodes.contains(&Next)
                        {
                            continue;
                        }
                        let DirectionValue =
                            (Next.0 - Current.0, Next.1 - Current.1, Next.2 - Current.2);
                        let TurnCost = if PriorDirection != InitialDirection
                            && DirectionValue != PriorDirection
                        {
                            BendPenalty
                        } else {
                            0
                        };
                        let NextPower = if PriorDirection != InitialDirection
                            && PriorDirection == DirectionValue
                            && DirectionValue.1 == 0
                            && DirectionValue.0.abs() + DirectionValue.2.abs() == 1
                        {
                            15
                        } else {
                            PowerRemaining.saturating_sub(1)
                        };
                        if NextPower == 0 {
                            continue;
                        }
                        let NextCost = Cost.saturating_add(1).saturating_add(TurnCost);
                        let NextState =
                            PoweredEscapeStateIndex(*NextNodeIndex, DirectionValue, NextPower);
                        if NextCost
                            >= Visits
                                .get(&NextState)
                                .map_or(usize::MAX, |Visit| Visit.CostValue())
                        {
                            continue;
                        }
                        let CurrentClaimRecord = Visits[&CurrentState].ClaimRecordValue();
                        let Some(NextClaims) = ExtendIndexedEscapeClaims(
                            &ClaimRecords,
                            Some(CurrentClaimRecord),
                            Some(Current),
                            Next,
                        ) else {
                            continue;
                        };
                        ClaimRecords.push(NextClaims);
                        let NextClaimRecord = ClaimRecords.len() - 1;
                        Visits.insert(
                            NextState,
                            PoweredEscapeVisit::New(NextCost, CurrentState, NextClaimRecord),
                        );
                        let Heuristic =
                            LayeredEscapeLowerBound(Next, DirectionValue, Ingress, BendPenalty);
                        Frontier.push(Reverse((
                            NextCost.saturating_add(Heuristic),
                            NextCost,
                            Next,
                            DirectionValue,
                            15u8.saturating_sub(NextPower),
                            *NextNodeIndex,
                            NextPower,
                        )));
                    }
                }
                if WorkCapExceeded
                    || DeadlineExceeded
                    || !PoweredCandidateFound
                    || AlternativeBlockedNodes.is_empty()
                {
                    break;
                }
            }
            if WorkCapExceeded || DeadlineExceeded {
                break;
            }
        }
        return (
            (
                RequestId,
                Candidates,
                ExpansionCount,
                !WorkCapExceeded && !DeadlineExceeded,
            ),
            WorkCapExceeded,
            DeadlineExceeded,
        );
    }

    let mut Frontier = BinaryHeap::from([Reverse((0usize, Start, InitialDirection))]);
    let mut BestCost = HashMap::from([(StartState, 0usize)]);
    let mut Parent: HashMap<(Position, Direction), Option<(Position, Direction)>> =
        HashMap::from([(StartState, None)]);
    let mut ClaimsByState: HashMap<(Position, Direction), Arc<EscapeClaimNode>> =
        HashMap::from([(StartState, StartClaims)]);

    while let Some(Reverse((Cost, Current, PriorDirection))) = Frontier.pop() {
        let CurrentState = (Current, PriorDirection);
        if BestCost.get(&CurrentState).copied() != Some(Cost) {
            continue;
        }
        if SharedExpansionLease.is_none() && ExpansionCount >= MaximumExpansionCount {
            WorkCapExceeded = true;
            break;
        }
        if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            DeadlineExceeded = true;
            break;
        }
        if !TryCountEscapeExpansion(
            &mut ExpansionCount,
            MaximumExpansionCount,
            SharedExpansionLease.as_deref_mut(),
        ) {
            WorkCapExceeded = true;
            break;
        }

        if Ingresses.contains(&Current)
            && !RejectedStateSet.contains(&CurrentState)
            && (!FirstPathPerIngress || RemainingIngresses.contains(&Current))
        {
            let mut ReversePath = Vec::new();
            let mut Cursor = Some(CurrentState);
            while let Some(State) = Cursor {
                ReversePath.push(State.0);
                Cursor = Parent.get(&State).copied().flatten();
            }
            ReversePath.reverse();
            Candidates.push((Current, PriorDirection, ReversePath));
            RemainingIngressStates.remove(&CurrentState);
            RemainingIngresses.remove(&Current);
            if (FirstPathPerIngress && RemainingIngresses.is_empty())
                || (!FirstPathPerIngress && RemainingIngressStates.is_empty())
            {
                break;
            }
        }

        if let Some(Neighbors) = Adjacency.get(&Current) {
            for Next in Neighbors {
                if !AllowsNode(Next) {
                    continue;
                }
                let DirectionValue = (Next.0 - Current.0, Next.1 - Current.1, Next.2 - Current.2);
                let TurnCost =
                    if PriorDirection != InitialDirection && DirectionValue != PriorDirection {
                        BendPenalty
                    } else {
                        0
                    };
                let NextCost = Cost.saturating_add(1).saturating_add(TurnCost);
                let NextState = (*Next, DirectionValue);
                if NextCost >= BestCost.get(&NextState).copied().unwrap_or(usize::MAX) {
                    continue;
                }
                let CurrentClaims = ClaimsByState
                    .get(&CurrentState)
                    .expect("settled escape state claims");
                let Some(NextClaims) =
                    ExtendEscapeClaims(Some(CurrentClaims.clone()), Some(Current), *Next)
                else {
                    continue;
                };
                BestCost.insert(NextState, NextCost);
                Parent.insert(NextState, Some(CurrentState));
                ClaimsByState.insert(NextState, NextClaims);
                Frontier.push(Reverse((NextCost, *Next, DirectionValue)));
            }
        }
    }

    (
        (
            RequestId,
            Candidates,
            ExpansionCount,
            !WorkCapExceeded && !DeadlineExceeded,
        ),
        WorkCapExceeded,
        DeadlineExceeded,
    )
}

/// Enumerate the least-cost path to every settled ingress direction-state.
///
/// Python retains exact redstone-claim validation.  Returning every settled
/// ingress state lets it reject a self-conflicting first path and consider
/// the same later direction states as the original bounded Python oracle.
pub(crate) fn BuildDerivedEscapeStatePathsWithDeadline(
    AdjacencyValues: Vec<(Position, Vec<Position>)>,
    Requests: Vec<EscapeRequest>,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
) -> (String, Vec<EscapeRequestResult>, usize, bool, bool) {
    BuildDerivedEscapeStatePathsWithMaximumYAndDeadline(
        AdjacencyValues,
        Requests,
        BendPenalty,
        MaximumExpansionCount,
        Deadline,
        None,
    )
}

pub(super) struct PreparedEscapeTraversalGraph {
    pub(super) Adjacency: HashMap<Position, Vec<Position>>,
    pub(super) IndexedGraph: IndexedEscapeGraph,
    pub(super) IndexedStateCount: usize,
    pub(super) CompleteRequestUpperBound: usize,
}

impl PreparedEscapeTraversalGraph {
    pub(super) fn New(AdjacencyValues: Vec<(Position, Vec<Position>)>) -> Self {
        let Adjacency: HashMap<Position, Vec<Position>> = AdjacencyValues
            .into_iter()
            .map(|(PositionValue, mut Neighbors)| {
                Neighbors.sort_unstable();
                Neighbors.dedup();
                (PositionValue, Neighbors)
            })
            .collect();
        let IndexedGraph = IndexedEscapeGraph::New(&Adjacency);
        let IndexedStateCount = IndexedGraph
            .Positions
            .len()
            .saturating_mul(ESCAPE_DIRECTION_STATE_COUNT);
        let CompleteRequestUpperBound = 1usize.saturating_add(
            Adjacency
                .values()
                .map(Vec::len)
                .fold(0usize, usize::saturating_add),
        );
        Self {
            Adjacency,
            IndexedGraph,
            IndexedStateCount,
            CompleteRequestUpperBound,
        }
    }
}

pub(super) fn BuildDerivedEscapeStatePathsWithMaximumYAndDeadline(
    AdjacencyValues: Vec<(Position, Vec<Position>)>,
    Requests: Vec<EscapeRequest>,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
    MaximumY: Option<i32>,
) -> (String, Vec<EscapeRequestResult>, usize, bool, bool) {
    let Prepared = PreparedEscapeTraversalGraph::New(AdjacencyValues);
    BuildDerivedEscapeStatePathsWithPreparedGraphAndDeadline(
        &Prepared,
        Requests,
        BendPenalty,
        MaximumExpansionCount,
        Deadline,
        MaximumY,
        true,
    )
}

pub(super) fn BuildDerivedEscapeStatePathsWithPreparedGraphAndDeadline(
    Prepared: &PreparedEscapeTraversalGraph,
    Requests: Vec<EscapeRequest>,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
    MaximumY: Option<i32>,
    AllowParallelRequests: bool,
) -> (String, Vec<EscapeRequestResult>, usize, bool, bool) {
    let Adjacency = &Prepared.Adjacency;
    let IndexedGraph = &Prepared.IndexedGraph;
    let IndexedStateCount = Prepared.IndexedStateCount;
    // A directional state is either the initial start state or one directed
    // graph edge.  First-path requests additionally certify up to three
    // powered alternatives per ingress, whose state includes one of fifteen
    // remaining-power values.  Derive each request's complete bound from its
    // immutable input before deciding whether independent requests can run
    // in parallel.
    let CompleteRequestUpperBound = Prepared.CompleteRequestUpperBound;
    let PoweredRequestUpperBound = 1usize.saturating_add(
        CompleteRequestUpperBound
            .saturating_sub(1)
            .saturating_mul(15),
    );
    let RequestUpperBounds = Requests
        .iter()
        .map(|Request| {
            if Request.6 {
                Request.2.len().saturating_mul(
                    CompleteRequestUpperBound
                        .saturating_add(PoweredRequestUpperBound.saturating_mul(3)),
                )
            } else {
                CompleteRequestUpperBound
            }
        })
        .collect::<Vec<_>>();

    if AllowParallelRequests
        && Requests
            .iter()
            .any(|Request| Request.6 && Request.2.len() > 1)
    {
        // First-path requests search every ingress independently and append
        // their candidates in declared ingress order. Split only that exact
        // independence boundary into deterministic work units, then shard
        // those units so each worker reuses one indexed workspace. Exact
        // chunk leases amortize shared accounting while preserving the
        // unchanged finite cap and exact final expansion count.
        let mut WorkUnits = Vec::<(usize, usize, EscapeRequest)>::new();
        let mut ExpectedUnitCounts = vec![0usize; Requests.len()];
        for (RequestIndex, Request) in Requests.iter().enumerate() {
            if Request.6 && Request.2.len() > 1 {
                for Ingress in &Request.2 {
                    let mut UnitRequest = Request.clone();
                    UnitRequest.2 = vec![*Ingress];
                    WorkUnits.push((WorkUnits.len(), RequestIndex, UnitRequest));
                    ExpectedUnitCounts[RequestIndex] += 1;
                }
            } else {
                WorkUnits.push((WorkUnits.len(), RequestIndex, Request.clone()));
                ExpectedUnitCounts[RequestIndex] = 1;
            }
        }

        let WorkerCount = RoutingThreadPool()
            .current_num_threads()
            .clamp(1, MAXIMUM_MEMBER_ESCAPE_SHARD_COUNT)
            .min(WorkUnits.len().max(1));
        let mut WorkShards = (0..WorkerCount)
            .map(|_| Vec::<(usize, usize, EscapeRequest)>::new())
            .collect::<Vec<_>>();
        for WorkUnit in WorkUnits {
            // Request construction groups related ingress variants next to
            // each other.  A plain modulo schedule therefore maps the same
            // expensive variant ordinal to one worker for thousands of
            // terminals.  Fold higher ordinal bits into the shard index so
            // the immutable work units remain deterministic while repeated
            // variant patterns are distributed across the existing pool.
            let MixedUnitIndex = WorkUnit.0 ^ (WorkUnit.0 >> 3) ^ (WorkUnit.0 >> 6);
            let ShardIndex = MixedUnitIndex % WorkerCount;
            WorkShards[ShardIndex].push(WorkUnit);
        }
        let SharedExpansionBudget = SharedEscapeExpansionBudget::New(MaximumExpansionCount);
        let ShardOutcomes = RoutingThreadPool().install(|| {
            WorkShards
                .into_par_iter()
                .map(|UnitChunk| {
                    let mut Workspace = IndexedEscapeWorkspace::New(IndexedStateCount);
                    let mut ExpansionLease =
                        SharedEscapeExpansionLease::New(&SharedExpansionBudget);
                    let mut Outcomes = Vec::with_capacity(UnitChunk.len());
                    for (UnitIndex, RequestIndex, Request) in UnitChunk {
                        let (Result, UnitWorkCap, UnitDeadline) = BuildOneDerivedEscapeRequest(
                            Adjacency,
                            IndexedGraph,
                            &mut Workspace,
                            Request,
                            BendPenalty,
                            MaximumExpansionCount,
                            Deadline.clone(),
                            MaximumY,
                            Some(&mut ExpansionLease),
                        );
                        Outcomes.push((UnitIndex, RequestIndex, Result, UnitWorkCap, UnitDeadline));
                        if UnitWorkCap || UnitDeadline {
                            break;
                        }
                    }
                    Outcomes
                })
                .collect::<Vec<_>>()
        });
        if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
            eprintln!(
                "native escape shard expansions: {:?}",
                ShardOutcomes
                    .iter()
                    .map(|Outcomes| Outcomes.iter().map(|Value| Value.2 .2).sum::<usize>())
                    .collect::<Vec<_>>()
            );
            let mut UnitExpansions = ShardOutcomes
                .iter()
                .flatten()
                .map(|Value| (Value.2 .2, Value.0, Value.2 .0.clone()))
                .collect::<Vec<_>>();
            UnitExpansions.sort_unstable_by(|First, Second| Second.cmp(First));
            eprintln!(
                "native escape largest units: {:?}",
                UnitExpansions.into_iter().take(24).collect::<Vec<_>>()
            );
        }
        let mut UnitOutcomes = ShardOutcomes.into_iter().flatten().collect::<Vec<_>>();
        UnitOutcomes.sort_unstable_by_key(|Value| Value.0);
        let ExpansionCount = SharedExpansionBudget.ExpansionCount();
        let WorkCapExceeded = UnitOutcomes
            .iter()
            .any(|(_UnitIndex, _RequestIndex, _Result, WorkCap, _Deadline)| *WorkCap);
        let DeadlineExceeded = UnitOutcomes
            .iter()
            .any(|(_UnitIndex, _RequestIndex, _Result, _WorkCap, DeadlineValue)| *DeadlineValue);

        let mut CompletedUnitCounts = vec![0usize; Requests.len()];
        let mut Results = Requests
            .iter()
            .map(|Request| (Request.0.clone(), Vec::new(), 0usize, true))
            .collect::<Vec<EscapeRequestResult>>();
        for (_UnitIndex, RequestIndex, UnitResult, _UnitWorkCap, _UnitDeadline) in UnitOutcomes {
            CompletedUnitCounts[RequestIndex] += 1;
            Results[RequestIndex].1.extend(UnitResult.1);
            Results[RequestIndex].2 = Results[RequestIndex].2.saturating_add(UnitResult.2);
            Results[RequestIndex].3 &= UnitResult.3;
        }
        for RequestIndex in 0..Results.len() {
            Results[RequestIndex].3 &=
                CompletedUnitCounts[RequestIndex] == ExpectedUnitCounts[RequestIndex];
        }
        let Status = if DeadlineExceeded {
            "DeadlineExceeded"
        } else if WorkCapExceeded {
            "WorkCapExceeded"
        } else {
            "Complete"
        };
        return (
            Status.to_string(),
            Results,
            ExpansionCount,
            WorkCapExceeded,
            DeadlineExceeded,
        );
    }
    let CompleteBatchUpperBound = RequestUpperBounds
        .iter()
        .copied()
        .fold(0usize, usize::saturating_add);
    let CanParallelizeCompleteBatch = AllowParallelRequests
        && Requests.len() > 1
        && CompleteBatchUpperBound <= MaximumExpansionCount;

    let (Results, ExpansionCount, WorkCapExceeded, DeadlineExceeded) =
        if CanParallelizeCompleteBatch {
            let RequestCount = Requests.len();
            let WorkerCount = RoutingThreadPool().current_num_threads().max(1);
            let ChunkSize = RequestCount.saturating_add(WorkerCount - 1) / WorkerCount;
            let Outcomes: Vec<(EscapeRequestResult, bool, bool)> =
                RoutingThreadPool().install(|| {
                    Requests
                        .into_par_iter()
                        .zip(RequestUpperBounds.into_par_iter())
                        .chunks(ChunkSize.max(1))
                        .map(|RequestChunk| {
                            let mut Workspace = IndexedEscapeWorkspace::New(IndexedStateCount);
                            RequestChunk
                                .into_iter()
                                .map(|(Request, RequestUpperBound)| {
                                    BuildOneDerivedEscapeRequest(
                                        Adjacency,
                                        IndexedGraph,
                                        &mut Workspace,
                                        Request,
                                        BendPenalty,
                                        RequestUpperBound,
                                        Deadline.clone(),
                                        MaximumY,
                                        None,
                                    )
                                })
                                .collect::<Vec<_>>()
                        })
                        .collect::<Vec<_>>()
                        .into_iter()
                        .flatten()
                        .collect()
                });
            let ExpansionCount = Outcomes
                .iter()
                .map(|(Result, _WorkCap, _Deadline)| Result.2)
                .sum();
            let WorkCapExceeded = Outcomes
                .iter()
                .any(|(_Result, WorkCap, _Deadline)| *WorkCap);
            let DeadlineExceeded = Outcomes
                .iter()
                .any(|(_Result, _WorkCap, DeadlineValue)| *DeadlineValue);
            (
                Outcomes.into_iter().map(|(Result, _, _)| Result).collect(),
                ExpansionCount,
                WorkCapExceeded,
                DeadlineExceeded,
            )
        } else if AllowParallelRequests && Requests.len() > 1 {
            // A complete portfolio upper bound can exceed the shared cap even
            // when every realized request is small. Schedule deterministic
            // prefix waves whose summed exact request bounds fit inside the
            // remaining cap. A wave therefore cannot overspend shared work,
            // while independent requests still use the native worker pool.
            let RequestValues = Requests
                .into_iter()
                .zip(RequestUpperBounds)
                .collect::<Vec<_>>();
            let mut Results = Vec::with_capacity(RequestValues.len());
            let mut ExpansionCount = 0usize;
            let mut WorkCapExceeded = false;
            let mut DeadlineExceeded = false;
            let mut RequestOffset = 0usize;
            while RequestOffset < RequestValues.len() {
                let RemainingExpansionCount = MaximumExpansionCount.saturating_sub(ExpansionCount);
                if RemainingExpansionCount == 0 {
                    WorkCapExceeded = true;
                    break;
                }
                let mut WaveEnd = RequestOffset;
                let mut WaveUpperBound = 0usize;
                while let Some((_Request, RequestUpperBound)) = RequestValues.get(WaveEnd) {
                    let NextUpperBound = WaveUpperBound.saturating_add(*RequestUpperBound);
                    if NextUpperBound > RemainingExpansionCount {
                        break;
                    }
                    WaveUpperBound = NextUpperBound;
                    WaveEnd += 1;
                }
                if WaveEnd == RequestOffset {
                    let mut Workspace = IndexedEscapeWorkspace::New(IndexedStateCount);
                    let (Result, RequestWorkCap, RequestDeadline) = BuildOneDerivedEscapeRequest(
                        Adjacency,
                        IndexedGraph,
                        &mut Workspace,
                        RequestValues[RequestOffset].0.clone(),
                        BendPenalty,
                        RemainingExpansionCount,
                        Deadline.clone(),
                        MaximumY,
                        None,
                    );
                    ExpansionCount = ExpansionCount.saturating_add(Result.2);
                    Results.push(Result);
                    WorkCapExceeded = RequestWorkCap;
                    DeadlineExceeded = RequestDeadline;
                    RequestOffset += 1;
                } else {
                    let WaveResults = RoutingThreadPool().install(|| {
                        RequestValues[RequestOffset..WaveEnd]
                            .par_iter()
                            .map(|(Request, RequestUpperBound)| {
                                let mut Workspace = IndexedEscapeWorkspace::New(IndexedStateCount);
                                BuildOneDerivedEscapeRequest(
                                    Adjacency,
                                    IndexedGraph,
                                    &mut Workspace,
                                    Request.clone(),
                                    BendPenalty,
                                    *RequestUpperBound,
                                    Deadline.clone(),
                                    MaximumY,
                                    None,
                                )
                            })
                            .collect::<Vec<_>>()
                    });
                    ExpansionCount = ExpansionCount.saturating_add(
                        WaveResults
                            .iter()
                            .map(|(Result, _WorkCap, _Deadline)| Result.2)
                            .sum::<usize>(),
                    );
                    WorkCapExceeded = WaveResults
                        .iter()
                        .any(|(_Result, WorkCap, _Deadline)| *WorkCap);
                    DeadlineExceeded = WaveResults
                        .iter()
                        .any(|(_Result, _WorkCap, DeadlineValue)| *DeadlineValue);
                    Results.extend(
                        WaveResults
                            .into_iter()
                            .map(|(Result, _WorkCap, _Deadline)| Result),
                    );
                    RequestOffset = WaveEnd;
                }
                if WorkCapExceeded || DeadlineExceeded {
                    break;
                }
            }
            (Results, ExpansionCount, WorkCapExceeded, DeadlineExceeded)
        } else {
            let mut Results = Vec::with_capacity(Requests.len());
            let mut ExpansionCount = 0usize;
            let mut WorkCapExceeded = false;
            let mut DeadlineExceeded = false;
            let mut Workspace = IndexedEscapeWorkspace::New(IndexedStateCount);
            for Request in Requests {
                let RemainingExpansionCount = MaximumExpansionCount.saturating_sub(ExpansionCount);
                let (Result, RequestWorkCap, RequestDeadline) = BuildOneDerivedEscapeRequest(
                    Adjacency,
                    IndexedGraph,
                    &mut Workspace,
                    Request,
                    BendPenalty,
                    RemainingExpansionCount,
                    Deadline.clone(),
                    MaximumY,
                    None,
                );
                ExpansionCount = ExpansionCount.saturating_add(Result.2);
                Results.push(Result);
                WorkCapExceeded = RequestWorkCap;
                DeadlineExceeded = RequestDeadline;
                if WorkCapExceeded || DeadlineExceeded {
                    break;
                }
            }
            (Results, ExpansionCount, WorkCapExceeded, DeadlineExceeded)
        };
    let Status = if DeadlineExceeded {
        "DeadlineExceeded"
    } else if WorkCapExceeded {
        "WorkCapExceeded"
    } else {
        "Complete"
    };
    (
        Status.to_string(),
        Results,
        ExpansionCount,
        WorkCapExceeded,
        DeadlineExceeded,
    )
}
