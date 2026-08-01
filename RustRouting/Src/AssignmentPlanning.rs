use crate::Assignment::{AssignCandidates, SortCandidatesWithDeadline};
use crate::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Models::{AssignmentCandidate, ClaimMask, ClaimMaskBuildError, RoutingAssignmentResult};
use pyo3::prelude::*;
use std::cell::RefCell;
use std::collections::{BTreeMap, HashMap};
use std::hash::{DefaultHasher, Hash, Hasher};
use std::sync::Arc;

const MAXIMUM_EXPANSIONS: usize = 1_000_000;
const MAXIMUM_CACHED_CLAIM_MASKS: usize = 100_000;

thread_local! {
    static ASSIGNMENT_CLAIM_MASK_CACHE:
        RefCell<HashMap<(String, u64), Arc<ClaimMask>>> =
        RefCell::new(HashMap::new());
}

pub(crate) type AssignmentCandidateValue = (
    String,
    String,
    Vec<usize>,
    Vec<usize>,
    Vec<usize>,
    Vec<usize>,
    i32,
    i32,
    i32,
    i32,
    i32,
);
pub(crate) type BaseAssignmentValue = (String, Vec<usize>, Vec<usize>, Vec<usize>, Vec<usize>);

pub(crate) fn DeadlineExceededAssignmentResult(CompletedWork: usize) -> RoutingAssignmentResult {
    RoutingAssignmentResult {
        Success: false,
        SelectedCandidateIds: Vec::new(),
        ExpansionCount: CompletedWork,
        BudgetExhausted: false,
        DeadlineExceeded: true,
        CompletedWork,
        FailureNet: None,
        ConflictSignals: Vec::new(),
        ConflictResourceIndices: Vec::new(),
        PairwiseIncompatibleSignals: Vec::new(),
        PairwiseCompatibilityComplete: false,
    }
}

fn BuildCandidateGroups(
    CandidateValues: Vec<AssignmentCandidateValue>,
    ResourceCount: usize,
    Deadline: &RuntimeDeadline,
) -> PyResult<Option<BTreeMap<String, Vec<AssignmentCandidate>>>> {
    let mut Groups: BTreeMap<String, Vec<AssignmentCandidate>> = BTreeMap::new();
    for (
        CandidateIndex,
        (
            Signal,
            CandidateId,
            Wire,
            Support,
            Air,
            Electrical,
            MaterialCost,
            FootprintGrowth,
            Length,
            BendCount,
            ViaCount,
        ),
    ) in CandidateValues.into_iter().enumerate()
    {
        if CandidateIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Ok(None);
        }
        let mut FingerprintState = DefaultHasher::new();
        ResourceCount.hash(&mut FingerprintState);
        Wire.hash(&mut FingerprintState);
        Support.hash(&mut FingerprintState);
        Air.hash(&mut FingerprintState);
        Electrical.hash(&mut FingerprintState);
        let CacheKey = (CandidateId.clone(), FingerprintState.finish());
        let CachedClaims = ASSIGNMENT_CLAIM_MASK_CACHE
            .with(|CacheCell| CacheCell.borrow().get(&CacheKey).cloned());
        let Claims = if let Some(Value) = CachedClaims {
            Value
        } else {
            let Value = match ClaimMask::FromIndicesWithDeadline(
                ResourceCount,
                &Wire,
                &Support,
                &Air,
                &Electrical,
                Deadline,
            ) {
                Ok(Value) => Arc::new(Value),
                Err(ClaimMaskBuildError::DeadlineExceeded) => return Ok(None),
                Err(ClaimMaskBuildError::IndexOutOfRange) => {
                    return Err(pyo3::exceptions::PyValueError::new_err(
                        "candidate references a resource outside the indexed graph",
                    ));
                }
            };
            ASSIGNMENT_CLAIM_MASK_CACHE.with(|CacheCell| {
                let mut Cache = CacheCell.borrow_mut();
                if Cache.len() > MAXIMUM_CACHED_CLAIM_MASKS {
                    Cache.clear();
                }
                Cache.insert(CacheKey, Arc::clone(&Value));
            });
            Value
        };
        Groups.entry(Signal).or_default().push(AssignmentCandidate {
            CandidateId,
            Claims,
            MaterialCost,
            FootprintGrowth,
            Length,
            BendCount,
            ViaCount,
        });
    }
    if Deadline.Check() {
        return Ok(None);
    }
    Ok(Some(Groups))
}

fn SortCandidateGroups(
    Groups: &mut BTreeMap<String, Vec<AssignmentCandidate>>,
    Deadline: &RuntimeDeadline,
) -> bool {
    for Values in Groups.values_mut() {
        if !SortCandidatesWithDeadline(Values, Deadline) {
            return false;
        }
    }
    !Deadline.Check()
}

fn BuildRemainingSignals(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    Deadline: &RuntimeDeadline,
) -> Option<Vec<String>> {
    let mut Remaining = Vec::with_capacity(Groups.len());
    for (Index, Signal) in Groups.keys().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return None;
        }
        Remaining.push(Signal.clone());
    }
    if Deadline.Check() {
        return None;
    }
    Some(Remaining)
}

fn SortSelectedCandidates(
    Selected: Vec<(String, String)>,
    Deadline: &RuntimeDeadline,
) -> Option<Vec<(String, String)>> {
    let mut SelectedBySignal = BTreeMap::new();
    for (Index, (Signal, CandidateId)) in Selected.into_iter().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return None;
        }
        SelectedBySignal.insert(Signal, CandidateId);
    }
    let mut Result = Vec::with_capacity(SelectedBySignal.len());
    for (Index, Value) in SelectedBySignal.into_iter().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return None;
        }
        Result.push(Value);
    }
    if Deadline.Check() {
        return None;
    }
    Some(Result)
}

#[allow(clippy::type_complexity)]
#[cfg(test)]
pub(crate) fn PlanAuthoritativeRoutesBoundedNative(
    CandidateValues: Vec<AssignmentCandidateValue>,
    ResourceCount: usize,
    MaximumExpansionCount: usize,
    MaximumRuntimeMilliseconds: u64,
) -> PyResult<RoutingAssignmentResult> {
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    PlanAuthoritativeRoutesWithDeadline(
        CandidateValues,
        ResourceCount,
        MaximumExpansionCount,
        Deadline,
    )
}

pub(crate) fn PlanAuthoritativeRoutesWithDeadline(
    CandidateValues: Vec<AssignmentCandidateValue>,
    ResourceCount: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
) -> PyResult<RoutingAssignmentResult> {
    if ResourceCount == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "resource count must be positive",
        ));
    }
    let Some(mut Groups) = BuildCandidateGroups(CandidateValues, ResourceCount, &Deadline)? else {
        return Ok(DeadlineExceededAssignmentResult(0));
    };
    if !SortCandidateGroups(&mut Groups, &Deadline) {
        return Ok(DeadlineExceededAssignmentResult(0));
    }
    let Some(Remaining) = BuildRemainingSignals(&Groups, &Deadline) else {
        return Ok(DeadlineExceededAssignmentResult(0));
    };
    let Some(Owned) = ClaimMask::NewWithDeadline(ResourceCount, &Deadline) else {
        return Ok(DeadlineExceededAssignmentResult(0));
    };
    let mut Selected = Vec::new();
    let mut ExpansionCount = 0usize;
    let mut FailureNet = None;
    let mut BudgetExhausted = false;
    let mut ConflictSignals = Vec::new();
    let mut ConflictResources = Vec::new();
    let mut PairwiseIncompatibleSignals = Vec::new();
    let mut PairwiseCompatibilityComplete = false;
    let Success = AssignCandidates(
        &Groups,
        &Remaining,
        &Owned,
        &BTreeMap::new(),
        &mut Selected,
        &mut ExpansionCount,
        MaximumExpansionCount.clamp(1, MAXIMUM_EXPANSIONS),
        &mut BudgetExhausted,
        &Deadline,
        &mut FailureNet,
        &mut ConflictSignals,
        &mut ConflictResources,
        &mut PairwiseIncompatibleSignals,
        &mut PairwiseCompatibilityComplete,
    );
    if Deadline.WasExceeded() {
        return Ok(DeadlineExceededAssignmentResult(ExpansionCount));
    }
    let Some(Selected) = SortSelectedCandidates(Selected, &Deadline) else {
        return Ok(DeadlineExceededAssignmentResult(ExpansionCount));
    };
    Ok(RoutingAssignmentResult {
        Success,
        SelectedCandidateIds: Selected,
        ExpansionCount,
        BudgetExhausted,
        DeadlineExceeded: Deadline.WasExceeded(),
        CompletedWork: ExpansionCount,
        FailureNet,
        ConflictSignals: if Success { Vec::new() } else { ConflictSignals },
        ConflictResourceIndices: ConflictResources,
        PairwiseIncompatibleSignals,
        PairwiseCompatibilityComplete,
    })
}

#[allow(clippy::type_complexity)]
#[cfg(test)]
pub(crate) fn PlanAuthoritativeRoutesWithBaseBoundedNative(
    CandidateValues: Vec<AssignmentCandidateValue>,
    BaseValues: Vec<BaseAssignmentValue>,
    ResourceCount: usize,
    MaximumExpansionCount: usize,
    MaximumRuntimeMilliseconds: u64,
) -> PyResult<RoutingAssignmentResult> {
    let Deadline = RuntimeDeadline::FromMilliseconds(Some(MaximumRuntimeMilliseconds))
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    PlanAuthoritativeRoutesWithBaseAndDeadline(
        CandidateValues,
        BaseValues,
        ResourceCount,
        MaximumExpansionCount,
        Deadline,
    )
}

pub(crate) fn PlanAuthoritativeRoutesWithBaseAndDeadline(
    CandidateValues: Vec<AssignmentCandidateValue>,
    BaseValues: Vec<BaseAssignmentValue>,
    ResourceCount: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
) -> PyResult<RoutingAssignmentResult> {
    if ResourceCount == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "resource count must be positive",
        ));
    }
    let mut BaseBySignal: BTreeMap<String, ClaimMask> = BTreeMap::new();
    for (BaseIndex, (Signal, Wire, Support, Air, Electrical)) in BaseValues.into_iter().enumerate()
    {
        if BaseIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            break;
        }
        let Claims = match ClaimMask::FromIndicesWithDeadline(
            ResourceCount,
            &Wire,
            &Support,
            &Air,
            &Electrical,
            &Deadline,
        ) {
            Ok(Value) => Value,
            Err(ClaimMaskBuildError::DeadlineExceeded) => {
                return Ok(DeadlineExceededAssignmentResult(0));
            }
            Err(ClaimMaskBuildError::IndexOutOfRange) => {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "base ownership references a resource outside the indexed graph",
                ));
            }
        };
        if let Some(Existing) = BaseBySignal.get_mut(&Signal) {
            if !Existing.UnionWithDeadline(&Claims, &Deadline) {
                return Ok(DeadlineExceededAssignmentResult(0));
            }
        } else {
            BaseBySignal.insert(Signal, Claims);
        }
    }
    if Deadline.WasExceeded() {
        return Ok(DeadlineExceededAssignmentResult(0));
    }
    let mut BaseSignals = Vec::with_capacity(BaseBySignal.len());
    for (Index, Signal) in BaseBySignal.keys().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Ok(DeadlineExceededAssignmentResult(0));
        }
        BaseSignals.push(Signal.clone());
    }
    for (Index, Signal) in BaseSignals.iter().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            break;
        }
        let Claims = &BaseBySignal[Signal];
        for OtherSignal in BaseSignals.iter().skip(Index + 1) {
            if Deadline.Check() {
                break;
            }
            let Conflicts =
                match Claims.ConflictsWithDeadline(&BaseBySignal[OtherSignal], &Deadline) {
                    Some(Value) => Value,
                    None => return Ok(DeadlineExceededAssignmentResult(0)),
                };
            if Conflicts {
                let Some(ConflictResourceIndices) =
                    Claims.ConflictIndicesWithDeadline(&BaseBySignal[OtherSignal], &Deadline)
                else {
                    return Ok(DeadlineExceededAssignmentResult(0));
                };
                return Ok(RoutingAssignmentResult {
                    Success: false,
                    SelectedCandidateIds: Vec::new(),
                    ExpansionCount: 0,
                    BudgetExhausted: false,
                    DeadlineExceeded: false,
                    CompletedWork: 0,
                    FailureNet: Some(Signal.clone()),
                    ConflictSignals: vec![Signal.clone(), OtherSignal.clone()],
                    ConflictResourceIndices,
                    PairwiseIncompatibleSignals: Vec::new(),
                    PairwiseCompatibilityComplete: false,
                });
            }
        }
        if Deadline.WasExceeded() {
            break;
        }
    }
    if Deadline.WasExceeded() {
        return Ok(DeadlineExceededAssignmentResult(0));
    }

    let Some(mut Groups) = BuildCandidateGroups(CandidateValues, ResourceCount, &Deadline)? else {
        return Ok(DeadlineExceededAssignmentResult(0));
    };
    if !SortCandidateGroups(&mut Groups, &Deadline) {
        return Ok(DeadlineExceededAssignmentResult(0));
    }
    let Some(Remaining) = BuildRemainingSignals(&Groups, &Deadline) else {
        return Ok(DeadlineExceededAssignmentResult(0));
    };
    let Some(Owned) = ClaimMask::NewWithDeadline(ResourceCount, &Deadline) else {
        return Ok(DeadlineExceededAssignmentResult(0));
    };
    let mut Selected = Vec::new();
    let mut ExpansionCount = 0usize;
    let mut FailureNet = None;
    let mut BudgetExhausted = false;
    let mut ConflictSignals = Vec::new();
    let mut ConflictResources = Vec::new();
    let mut PairwiseIncompatibleSignals = Vec::new();
    let mut PairwiseCompatibilityComplete = false;
    let Success = AssignCandidates(
        &Groups,
        &Remaining,
        &Owned,
        &BaseBySignal,
        &mut Selected,
        &mut ExpansionCount,
        MaximumExpansionCount.clamp(1, MAXIMUM_EXPANSIONS),
        &mut BudgetExhausted,
        &Deadline,
        &mut FailureNet,
        &mut ConflictSignals,
        &mut ConflictResources,
        &mut PairwiseIncompatibleSignals,
        &mut PairwiseCompatibilityComplete,
    );
    if Deadline.WasExceeded() {
        return Ok(DeadlineExceededAssignmentResult(ExpansionCount));
    }
    let Some(Selected) = SortSelectedCandidates(Selected, &Deadline) else {
        return Ok(DeadlineExceededAssignmentResult(ExpansionCount));
    };
    Ok(RoutingAssignmentResult {
        Success,
        SelectedCandidateIds: Selected,
        ExpansionCount,
        BudgetExhausted,
        DeadlineExceeded: Deadline.WasExceeded(),
        CompletedWork: ExpansionCount,
        FailureNet,
        ConflictSignals: if Success { Vec::new() } else { ConflictSignals },
        ConflictResourceIndices: ConflictResources,
        PairwiseIncompatibleSignals,
        PairwiseCompatibilityComplete,
    })
}

#[cfg(test)]
mod Tests {
    use super::*;

    fn CandidateValue() -> AssignmentCandidateValue {
        (
            "Signal".to_string(),
            "Candidate".to_string(),
            vec![1],
            Vec::new(),
            Vec::new(),
            vec![0, 1, 2],
            1,
            1,
            1,
            0,
            0,
        )
    }

    #[test]
    fn BoundedAssignmentReportsImmediateDeadlineSeparatelyFromBudget() {
        let Result = PlanAuthoritativeRoutesBoundedNative(vec![CandidateValue()], 4, 64, 0)
            .expect("zero-millisecond deadline should be a result, not an input error");
        assert!(!Result.Success);
        assert!(Result.DeadlineExceeded);
        assert!(!Result.BudgetExhausted);
        assert_eq!(Result.CompletedWork, 0);
        assert_eq!(Result.ExpansionCount, 0);
    }

    #[test]
    fn BoundedAssignmentCompletesWithinAvailableRuntime() {
        let Result = PlanAuthoritativeRoutesBoundedNative(vec![CandidateValue()], 4, 64, 1_000)
            .expect("valid bounded assignment should complete");
        assert!(Result.Success);
        assert!(!Result.DeadlineExceeded);
        assert!(!Result.BudgetExhausted);
        assert_eq!(Result.CompletedWork, 1);
        assert_eq!(
            Result.SelectedCandidateIds,
            vec![("Signal".to_string(), "Candidate".to_string())]
        );
        assert!(Result.ConflictSignals.is_empty());
    }

    #[test]
    fn HigherOrderFailureReportsSelectedStackAndEmptyDomainPairDeterministically() {
        let Candidate = |Signal: &str,
                         CandidateId: &str,
                         Wire: Vec<usize>,
                         Electrical: Vec<usize>|
         -> AssignmentCandidateValue {
            (
                Signal.to_string(),
                CandidateId.to_string(),
                Wire,
                Vec::new(),
                Vec::new(),
                Electrical,
                1,
                1,
                1,
                0,
                0,
            )
        };
        // Every signal pair has at least one compatible combination, but no
        // three-signal assignment exists. Selecting A0 restricts B to B0 and
        // C to C0; selecting B0 then empties C's domain.
        let CandidateValues = vec![
            Candidate("A", "A0", vec![1, 2], Vec::new()),
            Candidate("B", "B0", vec![3], Vec::new()),
            Candidate("B", "B1", Vec::new(), vec![1]),
            Candidate("C", "C0", Vec::new(), vec![3]),
            Candidate("C", "C1", Vec::new(), vec![2]),
        ];
        let First = PlanAuthoritativeRoutesBoundedNative(CandidateValues.clone(), 8, 128, 1_000)
            .expect("higher-order assignment failure should return diagnostics");
        let Second = PlanAuthoritativeRoutesBoundedNative(
            CandidateValues.into_iter().rev().collect(),
            8,
            128,
            1_000,
        )
        .expect("input order must not change assignment diagnostics");

        assert!(!First.Success);
        assert!(!First.BudgetExhausted);
        assert!(!First.DeadlineExceeded);
        assert_eq!(First.FailureNet, Some("C".to_string()));
        assert_eq!(
            First.ConflictSignals,
            vec!["A".to_string(), "B".to_string(), "C".to_string()]
        );
        assert_eq!(
            First.SelectedCandidateIds,
            vec![
                ("A".to_string(), "A0".to_string()),
                ("B".to_string(), "B0".to_string()),
            ]
        );
        assert_eq!(First.SelectedCandidateIds, Second.SelectedCandidateIds);
        assert_eq!(First.ConflictSignals, Second.ConflictSignals);
        assert_eq!(
            First.ConflictResourceIndices,
            Second.ConflictResourceIndices
        );
    }

    #[test]
    fn SuccessfulBacktrackingDoesNotExposeDiscardedBranchConflicts() {
        let CandidateValues = vec![
            (
                "A".to_string(),
                "A0-blocked".to_string(),
                vec![1],
                Vec::new(),
                Vec::new(),
                Vec::new(),
                1,
                1,
                1,
                0,
                0,
            ),
            (
                "A".to_string(),
                "A1-clear".to_string(),
                vec![5],
                Vec::new(),
                Vec::new(),
                Vec::new(),
                2,
                1,
                1,
                0,
                0,
            ),
            (
                "B".to_string(),
                "B0".to_string(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
                vec![1],
                1,
                1,
                1,
                0,
                0,
            ),
            (
                "B".to_string(),
                "B1".to_string(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
                vec![1],
                2,
                1,
                1,
                0,
                0,
            ),
        ];
        let Result = PlanAuthoritativeRoutesBoundedNative(CandidateValues, 8, 128, 1_000)
            .expect("the second A candidate provides a complete assignment");

        assert!(Result.Success);
        assert!(Result.ConflictSignals.is_empty());
    }

    #[test]
    fn BoundedBaseAssignmentReportsImmediateDeadline() {
        let Result = PlanAuthoritativeRoutesWithBaseBoundedNative(
            vec![CandidateValue()],
            vec![("Base".to_string(), vec![3], Vec::new(), Vec::new(), vec![3])],
            4,
            64,
            0,
        )
        .expect("zero-millisecond deadline should be a result, not an input error");
        assert!(!Result.Success);
        assert!(Result.DeadlineExceeded);
        assert!(!Result.BudgetExhausted);
        assert_eq!(Result.CompletedWork, 0);
    }
}
