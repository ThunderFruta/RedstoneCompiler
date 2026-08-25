use crate::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use pyo3::prelude::*;
use std::collections::{BTreeSet, HashMap};
use std::sync::Arc;

pub(crate) type Position = (i32, i32, i32);
pub(crate) type Edge = (Position, Position);
pub(crate) type Direction = (i32, i32, i32);
pub(crate) type SearchState = (Position, Direction, u8);
pub(crate) type Position2 = (i32, i32);
pub(crate) type RectilinearEdge = (Position2, Position2);

#[derive(Clone, Default)]
pub(crate) struct ClaimMask {
    Wire: Vec<usize>,
    Support: Vec<usize>,
    Air: Vec<usize>,
    Electrical: Vec<usize>,
}

impl ClaimMask {
    #[cfg(test)]
    pub(crate) fn New(_ResourceCount: usize) -> Self {
        Self::default()
    }

    #[cfg(test)]
    pub(crate) fn FromIndices(
        ResourceCount: usize,
        Wire: &[usize],
        Support: &[usize],
        Air: &[usize],
        Electrical: &[usize],
    ) -> Option<Self> {
        let Deadline = RuntimeDeadline::Unlimited();
        Self::FromIndicesWithDeadline(ResourceCount, Wire, Support, Air, Electrical, &Deadline).ok()
    }

    pub(crate) fn FromIndicesWithDeadline(
        ResourceCount: usize,
        Wire: &[usize],
        Support: &[usize],
        Air: &[usize],
        Electrical: &[usize],
        Deadline: &RuntimeDeadline,
    ) -> Result<Self, ClaimMaskBuildError> {
        if Deadline.Check() {
            return Err(ClaimMaskBuildError::DeadlineExceeded);
        }
        let mut Result = Self::default();
        let mut CompletedIndices = 0usize;
        for (Values, Mask) in [
            (Wire, &mut Result.Wire),
            (Support, &mut Result.Support),
            (Air, &mut Result.Air),
            (Electrical, &mut Result.Electrical),
        ] {
            for Index in Values {
                if CompletedIndices % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return Err(ClaimMaskBuildError::DeadlineExceeded);
                }
                if *Index >= ResourceCount {
                    return Err(ClaimMaskBuildError::IndexOutOfRange);
                }
                Mask.push(*Index);
                CompletedIndices += 1;
            }
            Mask.sort_unstable();
            Mask.dedup();
        }
        if Deadline.Check() {
            return Err(ClaimMaskBuildError::DeadlineExceeded);
        }
        Ok(Result)
    }

    pub(crate) fn NewWithDeadline(
        _ResourceCount: usize,
        Deadline: &RuntimeDeadline,
    ) -> Option<Self> {
        (!Deadline.Check()).then(Self::default)
    }

    pub(crate) fn IndexSets(&self) -> (&[usize], &[usize], &[usize], &[usize]) {
        (&self.Wire, &self.Support, &self.Air, &self.Electrical)
    }

    fn Intersects(First: &[usize], Second: &[usize]) -> bool {
        let mut FirstIndex = 0usize;
        let mut SecondIndex = 0usize;
        while FirstIndex < First.len() && SecondIndex < Second.len() {
            match First[FirstIndex].cmp(&Second[SecondIndex]) {
                std::cmp::Ordering::Less => FirstIndex += 1,
                std::cmp::Ordering::Greater => SecondIndex += 1,
                std::cmp::Ordering::Equal => return true,
            }
        }
        false
    }

    fn IntersectsWithDeadline(
        First: &[usize],
        Second: &[usize],
        Deadline: &RuntimeDeadline,
    ) -> Option<bool> {
        if Deadline.Check() {
            return None;
        }
        let mut FirstIndex = 0usize;
        let mut SecondIndex = 0usize;
        let mut Completed = 0usize;
        while FirstIndex < First.len() && SecondIndex < Second.len() {
            if Completed % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            match First[FirstIndex].cmp(&Second[SecondIndex]) {
                std::cmp::Ordering::Less => FirstIndex += 1,
                std::cmp::Ordering::Greater => SecondIndex += 1,
                std::cmp::Ordering::Equal => return Some(true),
            }
            Completed += 1;
        }
        if Deadline.Check() {
            return None;
        }
        Some(false)
    }

    pub(crate) fn Conflicts(&self, Other: &Self) -> bool {
        Self::Intersects(&self.Wire, &Other.Electrical)
            || Self::Intersects(&Other.Wire, &self.Electrical)
            || Self::Intersects(&self.Support, &Other.Wire)
            || Self::Intersects(&self.Support, &Other.Air)
            || Self::Intersects(&Other.Support, &self.Wire)
            || Self::Intersects(&Other.Support, &self.Air)
            || Self::Intersects(&self.Air, &Other.Wire)
            || Self::Intersects(&Other.Air, &self.Wire)
    }

    pub(crate) fn SameOwnerConflicts(&self, Other: &Self) -> bool {
        Self::Intersects(&self.Support, &Other.Wire)
            || Self::Intersects(&self.Support, &Other.Air)
            || Self::Intersects(&Other.Support, &self.Wire)
            || Self::Intersects(&Other.Support, &self.Air)
            || Self::Intersects(&self.Air, &Other.Wire)
            || Self::Intersects(&Other.Air, &self.Wire)
    }

    pub(crate) fn ConflictsWithDeadline(
        &self,
        Other: &Self,
        Deadline: &RuntimeDeadline,
    ) -> Option<bool> {
        for (First, Second) in [
            (&self.Wire, &Other.Electrical),
            (&Other.Wire, &self.Electrical),
            (&self.Support, &Other.Wire),
            (&self.Support, &Other.Air),
            (&Other.Support, &self.Wire),
            (&Other.Support, &self.Air),
            (&self.Air, &Other.Wire),
            (&Other.Air, &self.Wire),
        ] {
            match Self::IntersectsWithDeadline(First, Second, Deadline) {
                Some(true) => return Some(true),
                Some(false) => {}
                None => return None,
            }
        }
        Some(false)
    }

    /// Return only contradictions that remain illegal when both claim sets
    /// belong to the same logical signal. Electrical proximity and shared
    /// wire are legal merges; wire/support/air occupancy contradictions are
    /// not.
    pub(crate) fn SameOwnerConflictsWithDeadline(
        &self,
        Other: &Self,
        Deadline: &RuntimeDeadline,
    ) -> Option<bool> {
        for (First, Second) in [
            (&self.Support, &Other.Wire),
            (&self.Support, &Other.Air),
            (&Other.Support, &self.Wire),
            (&Other.Support, &self.Air),
            (&self.Air, &Other.Wire),
            (&Other.Air, &self.Wire),
        ] {
            match Self::IntersectsWithDeadline(First, Second, Deadline) {
                Some(true) => return Some(true),
                Some(false) => {}
                None => return None,
            }
        }
        Some(false)
    }

    pub(crate) fn UnionWithDeadline(&mut self, Other: &Self, Deadline: &RuntimeDeadline) -> bool {
        if Deadline.Check() {
            return false;
        }
        for (Target, Source) in [
            (&mut self.Wire, &Other.Wire),
            (&mut self.Support, &Other.Support),
            (&mut self.Air, &Other.Air),
            (&mut self.Electrical, &Other.Electrical),
        ] {
            let mut Combined = Vec::with_capacity(Target.len() + Source.len());
            let mut TargetIndex = 0usize;
            let mut SourceIndex = 0usize;
            while TargetIndex < Target.len() || SourceIndex < Source.len() {
                if Combined.len() % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return false;
                }
                let Next = match (Target.get(TargetIndex), Source.get(SourceIndex)) {
                    (Some(First), Some(Second)) if First <= Second => {
                        TargetIndex += 1;
                        *First
                    }
                    (Some(_), Some(Second)) => {
                        SourceIndex += 1;
                        *Second
                    }
                    (Some(First), None) => {
                        TargetIndex += 1;
                        *First
                    }
                    (None, Some(Second)) => {
                        SourceIndex += 1;
                        *Second
                    }
                    (None, None) => break,
                };
                if Combined.last() != Some(&Next) {
                    Combined.push(Next);
                }
            }
            *Target = Combined;
        }
        !Deadline.Check()
    }

    pub(crate) fn ConflictIndicesWithDeadline(
        &self,
        Other: &Self,
        Deadline: &RuntimeDeadline,
    ) -> Option<Vec<usize>> {
        if Deadline.Check() {
            return None;
        }
        let mut Result = BTreeSet::new();
        for (First, Second) in [
            (&self.Wire, &Other.Electrical),
            (&Other.Wire, &self.Electrical),
            (&self.Support, &Other.Wire),
            (&self.Support, &Other.Air),
            (&Other.Support, &self.Wire),
            (&Other.Support, &self.Air),
            (&self.Air, &Other.Wire),
            (&Other.Air, &self.Wire),
        ] {
            let mut FirstIndex = 0usize;
            let mut SecondIndex = 0usize;
            while FirstIndex < First.len() && SecondIndex < Second.len() {
                if (FirstIndex + SecondIndex) % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
                match First[FirstIndex].cmp(&Second[SecondIndex]) {
                    std::cmp::Ordering::Less => FirstIndex += 1,
                    std::cmp::Ordering::Greater => SecondIndex += 1,
                    std::cmp::Ordering::Equal => {
                        Result.insert(First[FirstIndex]);
                        FirstIndex += 1;
                        SecondIndex += 1;
                    }
                }
            }
        }
        if Deadline.Check() {
            return None;
        }
        Some(Result.into_iter().collect())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum ClaimMaskBuildError {
    DeadlineExceeded,
    IndexOutOfRange,
}

#[pyclass]
#[derive(Clone)]
pub(crate) struct PortalCandidate {
    #[pyo3(get)]
    pub(crate) PortalId: String,
    #[pyo3(get)]
    pub(crate) Target: Position,
    #[pyo3(get)]
    pub(crate) Path: Vec<Position>,
    #[pyo3(get)]
    pub(crate) WireClaims: Vec<Position>,
    #[pyo3(get)]
    pub(crate) SupportClaims: Vec<Position>,
    #[pyo3(get)]
    pub(crate) AirClaims: Vec<Position>,
    #[pyo3(get)]
    pub(crate) ElectricalClaims: Vec<Position>,
    #[pyo3(get)]
    pub(crate) Length: usize,
    #[pyo3(get)]
    pub(crate) BendCount: usize,
    #[pyo3(get)]
    pub(crate) ViaCount: usize,
}

#[pyclass]
pub(crate) struct PortalCandidateBatchResult {
    #[pyo3(get)]
    pub(crate) Candidates: Vec<Vec<PortalCandidate>>,
    #[pyo3(get)]
    pub(crate) CompletionMask: Vec<bool>,
    #[pyo3(get)]
    pub(crate) DeadlineExceeded: bool,
    #[pyo3(get)]
    pub(crate) CompletedWork: usize,
    #[pyo3(get)]
    pub(crate) TotalWork: usize,
}

#[pyclass]
pub(crate) struct RouteTreeBatchResult {
    #[pyo3(get)]
    pub(crate) RouteTrees: Vec<Option<Vec<Position>>>,
    #[pyo3(get)]
    pub(crate) RepeaterReservations: Vec<Vec<(Position, String)>>,
    #[pyo3(get)]
    pub(crate) CompletionMask: Vec<bool>,
    #[pyo3(get)]
    pub(crate) DeadlineExceeded: bool,
    #[pyo3(get)]
    pub(crate) CompletedWork: usize,
    #[pyo3(get)]
    pub(crate) TotalWork: usize,
}

/// Result of the one selected-world detailed generation/assignment batch.
/// Only trees named by `SelectedRequestIndices` are returned; unattempted
/// request shapes remain explicit incomplete work rather than being confused
/// with proved-unroutable shapes.
#[pyclass]
pub(crate) struct FactorizedRouteTreeSelectionResult {
    #[pyo3(get)]
    pub(crate) RouteTrees: Vec<Option<Vec<Position>>>,
    #[pyo3(get)]
    pub(crate) RepeaterReservations: Vec<Vec<(Position, String)>>,
    #[pyo3(get)]
    pub(crate) CompletionMask: Vec<bool>,
    #[pyo3(get)]
    pub(crate) SelectedRequestIndices: Vec<usize>,
    #[pyo3(get)]
    pub(crate) Success: bool,
    #[pyo3(get)]
    pub(crate) Complete: bool,
    #[pyo3(get)]
    pub(crate) DeadlineExceeded: bool,
    #[pyo3(get)]
    pub(crate) WorkCapExceeded: bool,
    #[pyo3(get)]
    pub(crate) AssignmentExpansionCount: usize,
    #[pyo3(get)]
    pub(crate) GeneratedRequestCount: usize,
    #[pyo3(get)]
    pub(crate) GeneratedRequestCountsBySignal: Vec<(String, usize)>,
    #[pyo3(get)]
    pub(crate) CandidateCountsBySignal: Vec<(String, usize)>,
    #[pyo3(get)]
    pub(crate) CompletedWork: usize,
    #[pyo3(get)]
    pub(crate) TotalWork: usize,
}

#[pyclass]
#[derive(Clone)]
pub(crate) struct RouteTreeSearchResult {
    #[pyo3(get)]
    pub(crate) Status: String,
    #[pyo3(get)]
    pub(crate) NoPathReason: String,
    #[pyo3(get)]
    pub(crate) Nodes: Vec<Position>,
    #[pyo3(get)]
    pub(crate) TargetPaths: Vec<(Position, Vec<Position>)>,
    #[pyo3(get)]
    pub(crate) BoundaryFrontierNodes: Vec<Position>,
    #[pyo3(get)]
    pub(crate) RepeaterReservations: Vec<(Position, String)>,
    #[pyo3(get)]
    pub(crate) ExpansionCount: usize,
    #[pyo3(get)]
    pub(crate) RepeaterRejectedCount: usize,
    #[pyo3(get)]
    pub(crate) RepeaterConstraintFailureCount: usize,
    #[pyo3(get)]
    pub(crate) ConflictResources: Vec<(String, Position)>,
    #[pyo3(get)]
    pub(crate) RejectedPathCount: usize,
    #[pyo3(get)]
    pub(crate) NoGoodCount: usize,
    #[pyo3(get)]
    pub(crate) ElapsedMilliseconds: u64,
    #[pyo3(get)]
    pub(crate) IsRouted: bool,
    #[pyo3(get)]
    pub(crate) IsBudgetExpired: bool,
}

/// One repeater-aware detailed route-tree request.  The batch entry point owns
/// the runtime limit so every request in a negotiated pass observes the same
/// absolute deadline.
pub(crate) type DetailedRouteTreeRequest = (
    Vec<Position>,
    Vec<Vec<Position>>,
    Vec<Position>,
    Vec<Position>,
    Vec<(i32, i32)>,
    Vec<(Position, i32)>,
    i32,
    i32,
    i32,
    i32,
    bool,
    usize,
);

/// Exact request components interned by Python before crossing the native
/// boundary.  Access payloads are shared by portal tuples and guide payloads
/// by corridor geometry; each request retains only the two table indices and
/// its scalar search policy.
pub(crate) type FactorizedRouteTreeAccessPayload = (
    Vec<Position>,
    Vec<Position>,
    Vec<Vec<Position>>,
    Vec<Vec<Position>>,
    Vec<Position>,
    Vec<Position>,
    Vec<Position>,
    Vec<Position>,
    Vec<Position>,
    Vec<Position>,
);
pub(crate) type FactorizedRouteTreeGuidePayload = (
    Vec<(i32, i32)>,
    Vec<(i32, i32)>,
    Vec<Position>,
    Vec<Vec<Position>>,
    Vec<(Position, String)>,
);
pub(crate) type FactorizedRouteTreeRequest = (usize, usize, i32, i32, i32, i32, usize);

/// One detailed request plus the immutable fixed claims that must coexist
/// with its newly generated tree.
pub(crate) type ClaimAwareDetailedRouteTreeRequest = (
    DetailedRouteTreeRequest,
    (Vec<Position>, Vec<Position>, Vec<Position>, Vec<Position>),
);

#[pyclass]
pub(crate) struct RouteTreeDetailedBatchResult {
    #[pyo3(get)]
    pub(crate) SearchResults: Vec<RouteTreeSearchResult>,
    #[pyo3(get)]
    pub(crate) DeadlineExceeded: bool,
    #[pyo3(get)]
    pub(crate) CompletedWork: usize,
    #[pyo3(get)]
    pub(crate) TotalWork: usize,
}

#[derive(Clone)]
pub(crate) struct AssignmentPoweredAccessConstraint {
    pub(crate) HasPoweredTreeWitness: bool,
    pub(crate) GraphAdjacency: Arc<HashMap<Position, Vec<Position>>>,
    pub(crate) TerminalVariables: Arc<Vec<String>>,
    pub(crate) DetachedSeedAccessPaths: Arc<Vec<Vec<Position>>>,
    pub(crate) SourceTerminalVariable: Option<String>,
    pub(crate) SourceDetachedAnchorIndex: Option<usize>,
    pub(crate) PreferredAccessCandidateTuples: Arc<Vec<Vec<(String, String)>>>,
}

#[derive(Clone)]
pub(crate) struct AssignmentCandidate {
    pub(crate) CandidateId: String,
    pub(crate) OwnerSignal: String,
    /// Named interface requirements are parsed once at decode time. Values
    /// may coexist only when every shared requirement has the same value.
    pub(crate) TemplateRequirements: Arc<Vec<(String, String)>>,
    /// Exact candidate pairs forbidden by a compact higher-order physical
    /// certificate.  Ordinary assignment domains leave this empty.
    pub(crate) ForbiddenCandidateIds: Arc<Vec<(String, String)>>,
    /// Ordered physical wire is retained only for compact access/guide
    /// candidates.  It lets the bounded assignment DFS validate the exact
    /// selected guide/stub tuple without expanding every Cartesian bundle.
    pub(crate) OrderedWire: Arc<Vec<Position>>,
    pub(crate) PoweredAccessConstraint: Option<Arc<AssignmentPoweredAccessConstraint>>,
    pub(crate) Claims: Arc<ClaimMask>,
    pub(crate) MaterialCost: i32,
    pub(crate) FootprintGrowth: i32,
    pub(crate) Length: i32,
    pub(crate) BendCount: i32,
    pub(crate) ViaCount: i32,
}

#[pyclass]
pub(crate) struct RoutingAssignmentResult {
    #[pyo3(get)]
    pub(crate) Success: bool,
    #[pyo3(get)]
    pub(crate) SelectedCandidateIds: Vec<(String, String)>,
    #[pyo3(get)]
    pub(crate) ExpansionCount: usize,
    #[pyo3(get)]
    pub(crate) BudgetExhausted: bool,
    #[pyo3(get)]
    pub(crate) DeadlineExceeded: bool,
    #[pyo3(get)]
    pub(crate) CompletedWork: usize,
    #[pyo3(get)]
    pub(crate) FailureNet: Option<String>,
    #[pyo3(get)]
    pub(crate) ConflictSignals: Vec<String>,
    #[pyo3(get)]
    pub(crate) ConflictResourceIndices: Vec<usize>,
    #[pyo3(get)]
    pub(crate) PairwiseIncompatibleSignals: Vec<(String, String)>,
    #[pyo3(get)]
    pub(crate) PairwiseCompatibilityComplete: bool,
}

/// Result of selecting one mutually exclusive, already-materialized routing
/// template and solving its ordinary authoritative track-assignment domain.
///
/// A successful result is a complete witness for the selected template.  It
/// is deliberately separate from `RoutingAssignmentResult`: the latter has
/// no placement/template identity and remains the stable single-resource-
/// graph binding used by ordinary routing.
#[pyclass]
pub(crate) struct TemplateRoutingAssignmentResult {
    #[pyo3(get)]
    pub(crate) Status: String,
    #[pyo3(get)]
    pub(crate) Success: bool,
    #[pyo3(get)]
    pub(crate) Complete: bool,
    #[pyo3(get)]
    pub(crate) Unsatisfiable: bool,
    #[pyo3(get)]
    pub(crate) IncompleteReason: String,
    #[pyo3(get)]
    pub(crate) SelectedTemplateId: Option<String>,
    #[pyo3(get)]
    pub(crate) SelectedTemplateObjective: Vec<i64>,
    #[pyo3(get)]
    pub(crate) SelectedCandidateIds: Vec<(String, String)>,
    #[pyo3(get)]
    pub(crate) ExpansionCount: usize,
    #[pyo3(get)]
    pub(crate) BudgetExhausted: bool,
    #[pyo3(get)]
    pub(crate) DeadlineExceeded: bool,
    #[pyo3(get)]
    pub(crate) CompletedWork: usize,
    #[pyo3(get)]
    pub(crate) FailureNet: Option<String>,
    #[pyo3(get)]
    pub(crate) ConflictSignals: Vec<String>,
    #[pyo3(get)]
    pub(crate) ConflictResourceIndices: Vec<usize>,
    #[pyo3(get)]
    pub(crate) PairwiseIncompatibleSignals: Vec<(String, String)>,
    #[pyo3(get)]
    pub(crate) PairwiseCompatibilityComplete: bool,
    #[pyo3(get)]
    pub(crate) AttemptedTemplateIds: Vec<String>,
    #[pyo3(get)]
    pub(crate) AttemptPairwiseIncompatibleSignals: Vec<(String, Vec<(String, String)>)>,
    #[pyo3(get)]
    pub(crate) AttemptFailureNets: Vec<(String, Option<String>)>,
    #[pyo3(get)]
    pub(crate) AttemptExpansionCounts: Vec<(String, usize)>,
    #[pyo3(get)]
    pub(crate) AttemptPartialCandidateIds: Vec<(String, Vec<(String, String)>)>,
    #[pyo3(get)]
    pub(crate) NonExhaustiveTemplateDomain: bool,
    #[pyo3(get)]
    pub(crate) CompactMaskTelemetry: Vec<(String, usize)>,
}

#[pyclass]
pub(crate) struct RoutingContext {
    pub(crate) Adjacency: HashMap<Position, Vec<Position>>,
    pub(crate) NodesByColumn: HashMap<(i32, i32), Vec<Position>>,
}

#[cfg(test)]
mod Tests {
    use super::*;
    use std::time::{Duration, Instant};

    #[test]
    fn LargeClaimConstructionStopsInsideIndexConversion() {
        let Indices = vec![0usize; 5_000_000];
        let Deadline = RuntimeDeadline::FromMilliseconds(Some(1)).unwrap();
        let Started = Instant::now();
        let Result = ClaimMask::FromIndicesWithDeadline(1, &Indices, &[], &[], &[], &Deadline);

        assert!(matches!(Result, Err(ClaimMaskBuildError::DeadlineExceeded)));
        assert!(Deadline.WasExceeded());
        assert!(Started.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn ExpiredDeadlineStopsLargeClaimOperations() {
        let First = ClaimMask::New(1_000_000);
        let Second = ClaimMask::New(1_000_000);
        let ConflictDeadline = RuntimeDeadline::FromMilliseconds(Some(0)).unwrap();
        let UnionDeadline = RuntimeDeadline::FromMilliseconds(Some(0)).unwrap();
        let IndicesDeadline = RuntimeDeadline::FromMilliseconds(Some(0)).unwrap();
        let mut UnionTarget = First.clone();

        assert_eq!(
            First.ConflictsWithDeadline(&Second, &ConflictDeadline),
            None,
        );
        assert!(!UnionTarget.UnionWithDeadline(&Second, &UnionDeadline));
        assert_eq!(
            First.ConflictIndicesWithDeadline(&Second, &IndicesDeadline),
            None,
        );
    }
}
