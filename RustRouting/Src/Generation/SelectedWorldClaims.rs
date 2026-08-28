use crate::Core::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Core::Models::{Position, Position2, RoutingContext};
use std::collections::{HashMap, HashSet};

pub(super) struct PreparedDetailedRouteGuide {
    pub(super) AllowedNodes: HashSet<Position>,
    pub(super) AllowedColumns: HashSet<Position2>,
    pub(super) UseColumnMembership: bool,
    pub(super) BoundaryBlockedNodes: HashSet<Position>,
    pub(super) NodeCosts: HashMap<Position, i32>,
    pub(super) ColumnCosts: HashMap<Position2, i32>,
    pub(super) PreferredColumns: Vec<(i32, i32)>,
    pub(super) ExactHintNodes: HashSet<Position>,
    pub(super) CertifiedPaths: Vec<Vec<Position>>,
    pub(super) CertifiedRepeaters: Vec<(Position, String)>,
    pub(super) GuidePenalty: i32,
}

pub(super) struct PreparedFactorizedRouteTreeAccess {
    pub(super) Starts: Vec<Position>,
    pub(super) SourceBranch: Vec<Position>,
    pub(super) TargetBranches: Vec<Vec<Position>>,
    pub(super) FrozenTargetBranches: Vec<Vec<Position>>,
    pub(super) RequiredNodes: HashSet<Position>,
    pub(super) BlockedNodes: HashSet<Position>,
    pub(super) MandatoryWire: HashSet<Position>,
    pub(super) MandatorySupport: HashSet<Position>,
    pub(super) MandatoryAir: HashSet<Position>,
    pub(super) MandatoryElectrical: HashSet<Position>,
}

#[derive(Clone)]
pub(super) struct ExactSelectedWorldRouteClaims {
    pub(super) Wire: HashSet<Position>,
    pub(super) Support: HashSet<Position>,
    pub(super) Air: HashSet<Position>,
    pub(super) Electrical: HashSet<Position>,
}

#[derive(Clone)]
pub(super) struct ExactSelectedWorldRouteCandidate {
    pub(super) RequestIndex: usize,
    pub(super) Nodes: Vec<Position>,
    pub(super) RepeaterReservations: Vec<(Position, String)>,
    pub(super) Claims: ExactSelectedWorldRouteClaims,
}

pub(super) fn PositionSetsIntersect(First: &HashSet<Position>, Second: &HashSet<Position>) -> bool {
    if First.len() <= Second.len() {
        First.iter().any(|Value| Second.contains(Value))
    } else {
        Second.iter().any(|Value| First.contains(Value))
    }
}

pub(super) fn ExactSelectedWorldClaimsConflict(
    First: &ExactSelectedWorldRouteClaims,
    Second: &ExactSelectedWorldRouteClaims,
) -> bool {
    PositionSetsIntersect(&First.Wire, &Second.Electrical)
        || PositionSetsIntersect(&Second.Wire, &First.Electrical)
        || PositionSetsIntersect(&First.Support, &Second.Wire)
        || PositionSetsIntersect(&First.Support, &Second.Air)
        || PositionSetsIntersect(&Second.Support, &First.Wire)
        || PositionSetsIntersect(&Second.Support, &First.Air)
        || PositionSetsIntersect(&First.Air, &Second.Wire)
        || PositionSetsIntersect(&Second.Air, &First.Wire)
}

pub(super) fn ExactSelectedWorldConflictResources(
    First: &ExactSelectedWorldRouteClaims,
    Second: &ExactSelectedWorldRouteClaims,
) -> Vec<(&'static str, Position)> {
    let mut Resources = Vec::new();
    for (Kind, FirstValues, SecondValues) in [
        ("wire-electrical", &First.Wire, &Second.Electrical),
        ("electrical-wire", &First.Electrical, &Second.Wire),
        ("support-wire", &First.Support, &Second.Wire),
        ("support-air", &First.Support, &Second.Air),
        ("wire-support", &First.Wire, &Second.Support),
        ("air-support", &First.Air, &Second.Support),
        ("air-wire", &First.Air, &Second.Wire),
        ("wire-air", &First.Wire, &Second.Air),
    ] {
        Resources.extend(
            FirstValues
                .intersection(SecondValues)
                .copied()
                .map(|PositionValue| (Kind, PositionValue)),
        );
    }
    Resources.sort_unstable();
    Resources.dedup();
    Resources
}

pub(super) fn RedstoneNeighborPositions(PositionValue: Position) -> [Position; 12] {
    let (X, Y, Z) = PositionValue;
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
}

pub(super) fn BuildExactSelectedWorldRouteClaims(
    Context: &RoutingContext,
    Nodes: &[Position],
    Access: &PreparedFactorizedRouteTreeAccess,
) -> ExactSelectedWorldRouteClaims {
    let mut Wire = Access.MandatoryWire.clone();
    Wire.extend(Nodes.iter().copied());
    let mut Support = Access.MandatorySupport.clone();
    let mut Air = Access.MandatoryAir.clone();
    let mut Electrical = Access.MandatoryElectrical.clone();
    for PositionValue in &Wire {
        Support.insert((PositionValue.0, PositionValue.1 - 1, PositionValue.2));
        Electrical.insert(*PositionValue);
        Electrical.extend(RedstoneNeighborPositions(*PositionValue));
    }
    for PositionValue in Nodes {
        for Neighbor in Context.Adjacency.get(PositionValue).into_iter().flatten() {
            if Neighbor.1 == PositionValue.1 || !Wire.contains(Neighbor) {
                continue;
            }
            let Lower = if PositionValue.1 < Neighbor.1 {
                *PositionValue
            } else {
                *Neighbor
            };
            Air.insert((Lower.0, Lower.1 + 1, Lower.2));
        }
    }
    ExactSelectedWorldRouteClaims {
        Wire,
        Support,
        Air,
        Electrical,
    }
}

pub(super) fn FindExactSelectedWorldMovableConflictNodes(
    Context: &RoutingContext,
    Candidate: &ExactSelectedWorldRouteCandidate,
    Access: &PreparedFactorizedRouteTreeAccess,
    Other: &ExactSelectedWorldRouteClaims,
) -> Vec<Position> {
    let CandidateNodeSet = Candidate.Nodes.iter().copied().collect::<HashSet<_>>();
    let mut Result = HashSet::new();
    for Node in CandidateNodeSet.iter().copied().filter(|Value| {
        // The fifth factorized payload field is the selected request's
        // additional allowed-node domain.  Python widens it with the
        // bounded access-repair corridor before this call; it is not an
        // immutable physical claim.  MandatoryWire is the authoritative
        // set of fixed access/portal conductors that a no-good may not
        // remove.
        !Access.MandatoryWire.contains(Value)
    }) {
        let Support = (Node.0, Node.1 - 1, Node.2);
        if Other.Electrical.contains(&Node)
            || Other.Support.contains(&Node)
            || Other.Air.contains(&Node)
            || Other.Wire.contains(&Support)
            || Other.Air.contains(&Support)
        {
            Result.insert(Node);
        }
        for Neighbor in Context.Adjacency.get(&Node).into_iter().flatten() {
            if Neighbor.1 == Node.1 || !CandidateNodeSet.contains(Neighbor) {
                continue;
            }
            let Lower = if Node.1 < Neighbor.1 { Node } else { *Neighbor };
            let RequiredAir = (Lower.0, Lower.1 + 1, Lower.2);
            if Other.Wire.contains(&RequiredAir) || Other.Support.contains(&RequiredAir) {
                Result.insert(Node);
                if !Access.MandatoryWire.contains(Neighbor) {
                    Result.insert(*Neighbor);
                }
            }
        }
    }
    let mut Values = Result.into_iter().collect::<Vec<_>>();
    Values.sort_unstable();
    Values
}

pub(super) fn BuildExactSelectedWorldForeignBlockedNodes(
    _Context: &RoutingContext,
    ForeignClaims: impl IntoIterator<Item = ExactSelectedWorldRouteClaims>,
) -> HashSet<Position> {
    let mut Blocked = HashSet::new();
    for Claims in ForeignClaims {
        Blocked.extend(Claims.Electrical.iter().copied());
        Blocked.extend(Claims.Support.iter().copied());
        Blocked.extend(Claims.Air.iter().copied());
        Blocked.extend(
            Claims
                .Wire
                .iter()
                .chain(&Claims.Air)
                .map(|Value| (Value.0, Value.1 + 1, Value.2)),
        );
    }
    Blocked
}

pub(super) fn SearchExactSelectedWorldAssignment(
    CandidateGroups: &[Vec<ExactSelectedWorldRouteCandidate>],
    GroupIndex: usize,
    SelectedCandidateIndices: &mut Vec<usize>,
    ExpansionCount: &mut usize,
    MaximumExpansionCount: usize,
    Deadline: &RuntimeDeadline,
) -> Option<Vec<usize>> {
    if Deadline.Check() || *ExpansionCount >= MaximumExpansionCount {
        return None;
    }
    if GroupIndex >= CandidateGroups.len() {
        return Some(SelectedCandidateIndices.clone());
    }
    for (CandidateIndex, Candidate) in CandidateGroups[GroupIndex].iter().enumerate() {
        if *ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return None;
        }
        *ExpansionCount = ExpansionCount.saturating_add(1);
        if *ExpansionCount > MaximumExpansionCount {
            return None;
        }
        if SelectedCandidateIndices.iter().enumerate().any(
            |(PriorGroupIndex, PriorCandidateIndex)| {
                ExactSelectedWorldClaimsConflict(
                    &Candidate.Claims,
                    &CandidateGroups[PriorGroupIndex][*PriorCandidateIndex].Claims,
                )
            },
        ) {
            continue;
        }
        SelectedCandidateIndices.push(CandidateIndex);
        if let Some(Result) = SearchExactSelectedWorldAssignment(
            CandidateGroups,
            GroupIndex + 1,
            SelectedCandidateIndices,
            ExpansionCount,
            MaximumExpansionCount,
            Deadline,
        ) {
            return Some(Result);
        }
        SelectedCandidateIndices.pop();
    }
    None
}
