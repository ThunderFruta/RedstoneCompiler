use crate::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Models::{Direction, Position};
use crate::RoutingThreadPool;
use rayon::prelude::*;
use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashMap, HashSet};
use std::sync::Arc;

#[derive(Debug)]
struct EscapeClaimNode {
    Wire: Position,
    Air: Option<Position>,
    WireBloom: [u64; 4],
    SupportBloom: [u64; 4],
    AirBloom: [u64; 4],
    Parent: Option<Arc<EscapeClaimNode>>,
}

fn EscapeClaimBloomIndex(PositionValue: Position) -> usize {
    // This is only a negative filter.  A bloom hit always falls through to
    // the exact parent-chain comparison below, so collisions cannot change
    // legality or deterministic path selection.
    let mut Value = (PositionValue.0 as u32 as u64).wrapping_mul(0x9E37_79B1_85EB_CA87);
    Value ^= (PositionValue.1 as u32 as u64).wrapping_mul(0xC2B2_AE3D_27D4_EB4F);
    Value ^= (PositionValue.2 as u32 as u64).wrapping_mul(0x1656_67B1_9E37_79F9);
    Value ^= Value >> 29;
    Value = Value.wrapping_mul(0x94D0_49BB_1331_11EB);
    (Value as usize) & 255
}

fn EscapeClaimBloomContains(Bloom: &[u64; 4], PositionValue: Position) -> bool {
    let Index = EscapeClaimBloomIndex(PositionValue);
    Bloom[Index / 64] & (1u64 << (Index % 64)) != 0
}

fn EscapeClaimBloomInsert(Bloom: &mut [u64; 4], PositionValue: Position) {
    let Index = EscapeClaimBloomIndex(PositionValue);
    Bloom[Index / 64] |= 1u64 << (Index % 64);
}

fn ExtendEscapeClaims(
    Parent: Option<Arc<EscapeClaimNode>>,
    Current: Option<Position>,
    Next: Position,
) -> Option<Arc<EscapeClaimNode>> {
    let NextSupport = (Next.0, Next.1 - 1, Next.2);
    let NextAir = Current.and_then(|CurrentValue| {
        if CurrentValue.1 == Next.1 {
            None
        } else {
            let Lower = if CurrentValue.1 < Next.1 {
                CurrentValue
            } else {
                Next
            };
            Some((Lower.0, Lower.1 + 1, Lower.2))
        }
    });
    if NextAir.is_some_and(|Air| Air == Next || Air == NextSupport) {
        return None;
    }
    let MustCheckExactClaims = Parent.as_ref().is_some_and(|Node| {
        EscapeClaimBloomContains(&Node.AirBloom, Next)
            || EscapeClaimBloomContains(&Node.SupportBloom, Next)
            || EscapeClaimBloomContains(&Node.WireBloom, NextSupport)
            || EscapeClaimBloomContains(&Node.AirBloom, NextSupport)
            || NextAir.is_some_and(|Air| {
                EscapeClaimBloomContains(&Node.WireBloom, Air)
                    || EscapeClaimBloomContains(&Node.SupportBloom, Air)
            })
    });
    if MustCheckExactClaims {
        let mut Cursor = Parent.clone();
        while let Some(Node) = Cursor {
            let ExistingSupport = (Node.Wire.0, Node.Wire.1 - 1, Node.Wire.2);
            if Node.Air == Some(Next)
                || ExistingSupport == Next
                || Node.Wire == NextSupport
                || Node.Air == Some(NextSupport)
                || NextAir.is_some_and(|Air| Air == Node.Wire || Air == ExistingSupport)
            {
                return None;
            }
            Cursor = Node.Parent.clone();
        }
    }
    let mut WireBloom = Parent.as_ref().map_or([0; 4], |Node| Node.WireBloom);
    let mut SupportBloom = Parent.as_ref().map_or([0; 4], |Node| Node.SupportBloom);
    let mut AirBloom = Parent.as_ref().map_or([0; 4], |Node| Node.AirBloom);
    EscapeClaimBloomInsert(&mut WireBloom, Next);
    EscapeClaimBloomInsert(&mut SupportBloom, NextSupport);
    if let Some(Air) = NextAir {
        EscapeClaimBloomInsert(&mut AirBloom, Air);
    }
    Some(Arc::new(EscapeClaimNode {
        Wire: Next,
        Air: NextAir,
        WireBloom,
        SupportBloom,
        AirBloom,
        Parent,
    }))
}

pub(crate) type EscapeRequest = (
    String,
    Position,
    Vec<Position>,
    Vec<(Position, Direction)>,
    Vec<Position>,
    Vec<Position>,
    bool,
);
pub(crate) type EscapePathCandidate = (Position, Direction, Vec<Position>);
pub(crate) type EscapeRequestResult = (String, Vec<EscapePathCandidate>, usize, bool);

fn BuildOneDerivedEscapeRequest(
    Adjacency: &HashMap<Position, Vec<Position>>,
    Request: EscapeRequest,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
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
    let Ingresses: HashSet<Position> = OrderedIngresses.iter().copied().collect();
    let RejectedStateSet: HashSet<(Position, Direction)> = RejectedStates.into_iter().collect();
    let AllowedNodeSet: HashSet<Position> = AllowedNodes.into_iter().collect();
    let InitialDirection: Direction = (0, 0, 0);
    let mut RemainingIngressStates: HashSet<(Position, Direction)> = Ingresses
        .iter()
        .flat_map(|Ingress| {
            let mut States: Vec<(Position, Direction)> = Adjacency
                .get(Ingress)
                .into_iter()
                .flatten()
                .filter(|Neighbor| AllowedNodeSet.contains(Neighbor))
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
        || !AllowedNodeSet.contains(&Start)
        || Ingresses
            .iter()
            .any(|Ingress| !Adjacency.contains_key(Ingress) || !AllowedNodeSet.contains(Ingress))
    {
        return ((RequestId, Candidates, 0, true), false, false);
    }

    let mut PrefixClaims: Option<Arc<EscapeClaimNode>> = None;
    let mut PriorPrefixPosition = None;
    for PositionValue in FixedPrefix {
        PrefixClaims = ExtendEscapeClaims(PrefixClaims, PriorPrefixPosition, PositionValue);
        if PrefixClaims.is_none() {
            return ((RequestId, Candidates, 0, true), false, false);
        }
        PriorPrefixPosition = Some(PositionValue);
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

    if FirstPathPerIngress {
        // Large face-restricted access graphs need target-directed search:
        // flooding their full directional state space for every terminal is
        // representation work when the caller consumes one path per ingress.
        // Fixed-band callers request the complete shared traversal below so
        // their exact directional-state bound remains authoritative.
        for Ingress in OrderedIngresses {
            let mut Frontier =
                BinaryHeap::from([Reverse((0usize, 0usize, Start, InitialDirection))]);
            let mut BestCost = HashMap::from([(StartState, 0usize)]);
            let mut Parent: HashMap<(Position, Direction), Option<(Position, Direction)>> =
                HashMap::from([(StartState, None)]);
            let mut ClaimsByState: HashMap<(Position, Direction), Arc<EscapeClaimNode>> =
                HashMap::from([(StartState, StartClaims.clone())]);
            let mut ReachedCandidate = None;
            while let Some(Reverse((_EstimatedCost, Cost, Current, PriorDirection))) =
                Frontier.pop()
            {
                let CurrentState = (Current, PriorDirection);
                if BestCost.get(&CurrentState).copied() != Some(Cost) {
                    continue;
                }
                if ExpansionCount >= MaximumExpansionCount {
                    WorkCapExceeded = true;
                    break;
                }
                if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    DeadlineExceeded = true;
                    break;
                }
                ExpansionCount += 1;
                if Current == Ingress && !RejectedStateSet.contains(&CurrentState) {
                    let mut ReversePath = Vec::new();
                    let mut Cursor = Some(CurrentState);
                    while let Some(State) = Cursor {
                        ReversePath.push(State.0);
                        Cursor = Parent.get(&State).copied().flatten();
                    }
                    ReversePath.reverse();
                    ReachedCandidate = Some((Current, PriorDirection, ReversePath));
                    break;
                }
                if let Some(Neighbors) = Adjacency.get(&Current) {
                    for Next in Neighbors {
                        if !AllowedNodeSet.contains(Next) {
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
                        let Heuristic = Next.0.abs_diff(Ingress.0) as usize
                            + Next.1.abs_diff(Ingress.1) as usize
                            + Next.2.abs_diff(Ingress.2) as usize;
                        Frontier.push(Reverse((
                            NextCost.saturating_add(Heuristic),
                            NextCost,
                            *Next,
                            DirectionValue,
                        )));
                    }
                }
            }
            if let Some(Candidate) = ReachedCandidate {
                Candidates.push(Candidate);
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
        if ExpansionCount >= MaximumExpansionCount {
            WorkCapExceeded = true;
            break;
        }
        if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            DeadlineExceeded = true;
            break;
        }
        ExpansionCount += 1;

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
                if !AllowedNodeSet.contains(Next) {
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
    let Adjacency: HashMap<Position, Vec<Position>> = AdjacencyValues
        .into_iter()
        .map(|(PositionValue, mut Neighbors)| {
            Neighbors.sort_unstable();
            Neighbors.dedup();
            (PositionValue, Neighbors)
        })
        .collect();
    // A directional state is either the initial start state or one directed
    // graph edge.  Compute that complete upper bound once for the immutable
    // graph instead of rebuilding an allowed-node set and rescanning the
    // graph for every batched terminal/prefix request.
    let CompleteRequestUpperBound = 1usize.saturating_add(
        Adjacency
            .values()
            .map(Vec::len)
            .fold(0usize, usize::saturating_add),
    );
    let CompleteBatchUpperBound = CompleteRequestUpperBound.saturating_mul(Requests.len());
    let CanParallelizeCompleteBatch =
        Requests.len() > 1 && CompleteBatchUpperBound <= MaximumExpansionCount;

    let (Results, ExpansionCount, WorkCapExceeded, DeadlineExceeded) =
        if CanParallelizeCompleteBatch {
            let Outcomes: Vec<(EscapeRequestResult, bool, bool)> =
                RoutingThreadPool().install(|| {
                    Requests
                        .into_par_iter()
                        .map(|Request| {
                            BuildOneDerivedEscapeRequest(
                                &Adjacency,
                                Request,
                                BendPenalty,
                                CompleteRequestUpperBound,
                                Deadline.clone(),
                            )
                        })
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
        } else {
            let mut Results = Vec::with_capacity(Requests.len());
            let mut ExpansionCount = 0usize;
            let mut WorkCapExceeded = false;
            let mut DeadlineExceeded = false;
            for Request in Requests {
                let RemainingExpansionCount = MaximumExpansionCount.saturating_sub(ExpansionCount);
                let (Result, RequestWorkCap, RequestDeadline) = BuildOneDerivedEscapeRequest(
                    &Adjacency,
                    Request,
                    BendPenalty,
                    RemainingExpansionCount,
                    Deadline.clone(),
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

#[cfg(test)]
mod Tests {
    use super::*;

    #[test]
    fn EnumeratesIngressDirectionStatesDeterministically() {
        let Result = BuildDerivedEscapeStatePathsWithDeadline(
            vec![
                ((0, 0, 0), vec![(1, 0, 0), (0, 0, 1)]),
                ((1, 0, 0), vec![(0, 0, 0), (1, 0, 1)]),
                ((0, 0, 1), vec![(0, 0, 0), (1, 0, 1)]),
                ((1, 0, 1), vec![(1, 0, 0), (0, 0, 1)]),
            ],
            vec![(
                "request".to_string(),
                (0, 0, 0),
                vec![(1, 0, 1)],
                Vec::new(),
                Vec::new(),
                vec![(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1)],
                false,
            )],
            4,
            128,
            RuntimeDeadline::Unlimited(),
        );
        assert_eq!(Result.0, "Complete");
        assert_eq!(Result.1.len(), 1);
        assert_eq!(Result.1[0].1.len(), 2);
        assert_eq!(Result.1[0].1[0].2, vec![(0, 0, 0), (1, 0, 0), (1, 0, 1)]);

        let Rejected = BuildDerivedEscapeStatePathsWithDeadline(
            vec![
                ((0, 0, 0), vec![(1, 0, 0), (0, 0, 1)]),
                ((1, 0, 0), vec![(0, 0, 0), (1, 0, 1)]),
                ((0, 0, 1), vec![(0, 0, 0), (1, 0, 1)]),
                ((1, 0, 1), vec![(1, 0, 0), (0, 0, 1)]),
            ],
            vec![(
                "request".to_string(),
                (0, 0, 0),
                vec![(1, 0, 1)],
                vec![((1, 0, 1), (0, 0, 1))],
                Vec::new(),
                vec![(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1)],
                false,
            )],
            4,
            128,
            RuntimeDeadline::Unlimited(),
        );
        assert_eq!(Rejected.1[0].1[0].2, vec![(0, 0, 0), (0, 0, 1), (1, 0, 1)]);
    }

    #[test]
    fn WorkCapIsIncompleteNotUnsatisfiable() {
        let Result = BuildDerivedEscapeStatePathsWithDeadline(
            vec![((0, 0, 0), vec![(1, 0, 0)]), ((1, 0, 0), vec![(0, 0, 0)])],
            vec![(
                "request".to_string(),
                (0, 0, 0),
                vec![(1, 0, 0)],
                Vec::new(),
                Vec::new(),
                vec![(0, 0, 0), (1, 0, 0)],
                false,
            )],
            4,
            1,
            RuntimeDeadline::Unlimited(),
        );
        assert_eq!(Result.0, "WorkCapExceeded");
        assert!(Result.3);
        assert!(!Result.4);
    }
}
