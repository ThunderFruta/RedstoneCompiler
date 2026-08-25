use crate::Assignment::{AssignCandidates, ParseContractRequirements, SortCandidatesWithDeadline};
use crate::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Models::{
    AssignmentCandidate, ClaimMask, ClaimMaskBuildError, Position, RoutingAssignmentResult,
    TemplateRoutingAssignmentResult,
};
use pyo3::prelude::*;
use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::hash::{DefaultHasher, Hash, Hasher};
use std::sync::atomic::AtomicUsize;
use std::sync::Arc;
use std::time::Instant;

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
    String,
    String,
);
pub(crate) type BaseAssignmentValue = (String, Vec<usize>, Vec<usize>, Vec<usize>, Vec<usize>);
/// One immutable, complete routing-template assignment domain supplied by
/// Python after it has materialized the template's own resource graph.
///
/// Resource indices are intentionally local to each template: templates are
/// mutually exclusive, so `ClaimMask` comparisons are only meaningful inside
/// the selected template's indexed graph.  `RequiredSignals` preserves an
/// explicit empty complete domain, which flattened candidate payloads alone
/// cannot represent.
pub(crate) type TemplateAssignmentDomainValue = (
    String,
    Vec<i64>,
    usize,
    Vec<String>,
    Vec<AssignmentCandidateValue>,
    Vec<BaseAssignmentValue>,
);
pub(crate) type CompactClaimPrimitiveValue = (
    String,
    Vec<Position>,
    Vec<Position>,
    Vec<Position>,
    Vec<Position>,
    Vec<Position>,
);
pub(crate) type CompactFactorValue = (
    String,
    String,
    String,
    Vec<usize>,
    i32,
    i32,
    i32,
    i32,
    i32,
    String,
    String,
);
pub(crate) type CompactFactorMemberValue = (String, Vec<i64>, Vec<String>, Vec<usize>);

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

fn BudgetExceededAssignmentResult(CompletedWork: usize) -> RoutingAssignmentResult {
    RoutingAssignmentResult {
        Success: false,
        SelectedCandidateIds: Vec::new(),
        ExpansionCount: CompletedWork,
        BudgetExhausted: true,
        DeadlineExceeded: false,
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
            TemplateKey,
            OwnerSignal,
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
            TemplateRequirements: ParseContractRequirements(&TemplateKey),
            ForbiddenCandidateIds: Arc::new(Vec::new()),
            OrderedWire: Arc::new(Vec::new()),
            PoweredAccessConstraint: None,
            OwnerSignal,
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
    PlanAuthoritativeRoutesWithInitialExpansionAndDeadline(
        CandidateValues,
        ResourceCount,
        0,
        MaximumExpansionCount,
        Deadline,
    )
}

/// Run one assignment domain while continuing an already-spent expansion
/// counter.  The aggregate template selector uses this to make the existing
/// exact `ClaimMask` search share one fixed budget without changing ordinary
/// single-template binding semantics.
pub(crate) fn PlanAuthoritativeRoutesWithInitialExpansionAndDeadline(
    CandidateValues: Vec<AssignmentCandidateValue>,
    ResourceCount: usize,
    InitialExpansionCount: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
) -> PyResult<RoutingAssignmentResult> {
    if ResourceCount == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "resource count must be positive",
        ));
    }
    let EffectiveMaximumExpansionCount = MaximumExpansionCount.clamp(1, MAXIMUM_EXPANSIONS);
    if InitialExpansionCount >= EffectiveMaximumExpansionCount {
        return Ok(BudgetExceededAssignmentResult(InitialExpansionCount));
    }
    let Some(mut Groups) = BuildCandidateGroups(CandidateValues, ResourceCount, &Deadline)? else {
        return Ok(DeadlineExceededAssignmentResult(InitialExpansionCount));
    };
    PlanAuthoritativeCandidateGroupsWithInitialExpansionAndDeadline(
        &mut Groups,
        ResourceCount,
        InitialExpansionCount,
        EffectiveMaximumExpansionCount,
        Deadline,
        true,
        true,
        None,
    )
}

pub(crate) fn PlanAuthoritativeCandidateGroupsWithInitialExpansionAndDeadline(
    Groups: &mut BTreeMap<String, Vec<AssignmentCandidate>>,
    ResourceCount: usize,
    InitialExpansionCount: usize,
    EffectiveMaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
    UsePairwiseCompatibilityIndex: bool,
    CollectConflictResources: bool,
    SharedExpansionCount: Option<&AtomicUsize>,
) -> PyResult<RoutingAssignmentResult> {
    PlanAuthoritativeCandidateGroupsWithInitialExpansionDeadlineAndCrossAir(
        Groups,
        ResourceCount,
        InitialExpansionCount,
        EffectiveMaximumExpansionCount,
        Deadline,
        UsePairwiseCompatibilityIndex,
        CollectConflictResources,
        SharedExpansionCount,
        None,
    )
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn PlanAuthoritativeCandidateGroupsWithInitialExpansionDeadlineAndCrossAir(
    Groups: &mut BTreeMap<String, Vec<AssignmentCandidate>>,
    ResourceCount: usize,
    InitialExpansionCount: usize,
    EffectiveMaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
    UsePairwiseCompatibilityIndex: bool,
    CollectConflictResources: bool,
    SharedExpansionCount: Option<&AtomicUsize>,
    CrossAirByWire: Option<&[Vec<(usize, usize)>]>,
) -> PyResult<RoutingAssignmentResult> {
    if !SortCandidateGroups(Groups, &Deadline) {
        return Ok(DeadlineExceededAssignmentResult(InitialExpansionCount));
    }
    let Some(Remaining) = BuildRemainingSignals(Groups, &Deadline) else {
        return Ok(DeadlineExceededAssignmentResult(InitialExpansionCount));
    };
    let Some(Owned) = ClaimMask::NewWithDeadline(ResourceCount, &Deadline) else {
        return Ok(DeadlineExceededAssignmentResult(InitialExpansionCount));
    };
    let mut Selected = Vec::new();
    let mut ExpansionCount = InitialExpansionCount;
    let mut FailureNet = None;
    let mut BudgetExhausted = false;
    let mut ConflictSignals = Vec::new();
    let mut ConflictResources = Vec::new();
    let mut PairwiseIncompatibleSignals = Vec::new();
    let mut PairwiseCompatibilityComplete = false;
    let Success = if UsePairwiseCompatibilityIndex {
        AssignCandidates(
            Groups,
            &Remaining,
            &Owned,
            &BTreeMap::new(),
            &mut Selected,
            &mut ExpansionCount,
            EffectiveMaximumExpansionCount,
            &mut BudgetExhausted,
            &Deadline,
            true,
            &mut FailureNet,
            &mut ConflictSignals,
            &mut ConflictResources,
            &mut PairwiseIncompatibleSignals,
            &mut PairwiseCompatibilityComplete,
            CollectConflictResources,
            SharedExpansionCount,
            CrossAirByWire,
        )
    } else {
        AssignCandidates(
            Groups,
            &Remaining,
            &Owned,
            &BTreeMap::new(),
            &mut Selected,
            &mut ExpansionCount,
            EffectiveMaximumExpansionCount,
            &mut BudgetExhausted,
            &Deadline,
            false,
            &mut FailureNet,
            &mut ConflictSignals,
            &mut ConflictResources,
            &mut PairwiseIncompatibleSignals,
            &mut PairwiseCompatibilityComplete,
            CollectConflictResources,
            SharedExpansionCount,
            CrossAirByWire,
        )
    };
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
    PlanAuthoritativeRoutesWithBaseAndInitialExpansionAndDeadline(
        CandidateValues,
        BaseValues,
        ResourceCount,
        0,
        MaximumExpansionCount,
        Deadline,
    )
}

/// Base-claim variant of
/// `PlanAuthoritativeRoutesWithInitialExpansionAndDeadline`.
pub(crate) fn PlanAuthoritativeRoutesWithBaseAndInitialExpansionAndDeadline(
    CandidateValues: Vec<AssignmentCandidateValue>,
    BaseValues: Vec<BaseAssignmentValue>,
    ResourceCount: usize,
    InitialExpansionCount: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
) -> PyResult<RoutingAssignmentResult> {
    if ResourceCount == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "resource count must be positive",
        ));
    }
    let EffectiveMaximumExpansionCount = MaximumExpansionCount.clamp(1, MAXIMUM_EXPANSIONS);
    if InitialExpansionCount >= EffectiveMaximumExpansionCount {
        return Ok(BudgetExceededAssignmentResult(InitialExpansionCount));
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
                return Ok(DeadlineExceededAssignmentResult(InitialExpansionCount));
            }
            Err(ClaimMaskBuildError::IndexOutOfRange) => {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "base ownership references a resource outside the indexed graph",
                ));
            }
        };
        if let Some(Existing) = BaseBySignal.get_mut(&Signal) {
            if !Existing.UnionWithDeadline(&Claims, &Deadline) {
                return Ok(DeadlineExceededAssignmentResult(InitialExpansionCount));
            }
        } else {
            BaseBySignal.insert(Signal, Claims);
        }
    }
    if Deadline.WasExceeded() {
        return Ok(DeadlineExceededAssignmentResult(InitialExpansionCount));
    }
    let mut BaseSignals = Vec::with_capacity(BaseBySignal.len());
    for (Index, Signal) in BaseBySignal.keys().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Ok(DeadlineExceededAssignmentResult(InitialExpansionCount));
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
                    None => return Ok(DeadlineExceededAssignmentResult(InitialExpansionCount)),
                };
            if Conflicts {
                let Some(ConflictResourceIndices) =
                    Claims.ConflictIndicesWithDeadline(&BaseBySignal[OtherSignal], &Deadline)
                else {
                    return Ok(DeadlineExceededAssignmentResult(InitialExpansionCount));
                };
                return Ok(RoutingAssignmentResult {
                    Success: false,
                    SelectedCandidateIds: Vec::new(),
                    ExpansionCount: InitialExpansionCount,
                    BudgetExhausted: false,
                    DeadlineExceeded: false,
                    CompletedWork: InitialExpansionCount,
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
        return Ok(DeadlineExceededAssignmentResult(InitialExpansionCount));
    }

    let Some(mut Groups) = BuildCandidateGroups(CandidateValues, ResourceCount, &Deadline)? else {
        return Ok(DeadlineExceededAssignmentResult(InitialExpansionCount));
    };
    if !SortCandidateGroups(&mut Groups, &Deadline) {
        return Ok(DeadlineExceededAssignmentResult(InitialExpansionCount));
    }
    let Some(Remaining) = BuildRemainingSignals(&Groups, &Deadline) else {
        return Ok(DeadlineExceededAssignmentResult(InitialExpansionCount));
    };
    let Some(Owned) = ClaimMask::NewWithDeadline(ResourceCount, &Deadline) else {
        return Ok(DeadlineExceededAssignmentResult(InitialExpansionCount));
    };
    let mut Selected = Vec::new();
    let mut ExpansionCount = InitialExpansionCount;
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
        EffectiveMaximumExpansionCount,
        &mut BudgetExhausted,
        &Deadline,
        true,
        &mut FailureNet,
        &mut ConflictSignals,
        &mut ConflictResources,
        &mut PairwiseIncompatibleSignals,
        &mut PairwiseCompatibilityComplete,
        true,
        None,
        None,
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

/// Solve one fixed set of mutually exclusive physical assignment domains.
///
/// Each member owns a local resource index, so their masks must never be
/// flattened into one capacity problem.  The *choice* between them is still
/// one bounded decision: this function orders the immutable objectives,
/// spends one shared expansion counter and deadline, and returns the first
/// successful objective group.  A complete core in one member permits the
/// next predeclared member; it is not a regenerated route attempt.
pub(crate) fn SolveTemplateAssignmentDomainsWithDeadline(
    mut Domains: Vec<TemplateAssignmentDomainValue>,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
    NonExhaustiveTemplateDomain: bool,
) -> PyResult<TemplateRoutingAssignmentResult> {
    Domains.sort_by(|First, Second| First.1.cmp(&Second.1).then_with(|| First.0.cmp(&Second.0)));
    let EffectiveMaximumExpansionCount = MaximumExpansionCount.clamp(1, MAXIMUM_EXPANSIONS);
    let mut ExpansionCount = 0usize;
    let mut AttemptedTemplateIds = Vec::new();
    let mut FirstConflictSignals = Vec::new();
    let mut FirstConflictResourceIndices = Vec::new();
    let mut FirstPairwiseIncompatibleSignals = Vec::new();
    let mut AttemptPairwiseIncompatibleSignals = Vec::new();
    let mut AttemptFailureNets = Vec::new();
    let mut AttemptExpansionCounts = Vec::new();
    let mut AttemptPartialCandidateIds = Vec::new();
    let mut Index = 0usize;

    while Index < Domains.len() {
        if Deadline.Check() {
            return Ok(TemplateRoutingAssignmentResult {
                Status: "Incomplete".to_string(),
                Success: false,
                Complete: false,
                Unsatisfiable: false,
                IncompleteReason: "assignment-deadline".to_string(),
                SelectedTemplateId: None,
                SelectedTemplateObjective: Vec::new(),
                SelectedCandidateIds: Vec::new(),
                ExpansionCount,
                BudgetExhausted: false,
                DeadlineExceeded: true,
                CompletedWork: ExpansionCount,
                FailureNet: None,
                ConflictSignals: FirstConflictSignals,
                ConflictResourceIndices: FirstConflictResourceIndices,
                PairwiseIncompatibleSignals: FirstPairwiseIncompatibleSignals,
                PairwiseCompatibilityComplete: false,
                AttemptedTemplateIds,
                AttemptPairwiseIncompatibleSignals,
                AttemptFailureNets,
                AttemptExpansionCounts,
                AttemptPartialCandidateIds,
                NonExhaustiveTemplateDomain,
                CompactMaskTelemetry: Vec::new(),
            });
        }
        let Objective = Domains[Index].1.clone();
        let mut SuccessfulMembers: Vec<(String, Vec<i64>, RoutingAssignmentResult)> = Vec::new();
        while Index < Domains.len() && Domains[Index].1 == Objective {
            let (
                TemplateId,
                TemplateObjective,
                ResourceCount,
                RequiredSignals,
                CandidateValues,
                BaseValues,
            ) = Domains[Index].clone();
            AttemptedTemplateIds.push(TemplateId.clone());
            if ResourceCount == 0 {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "template assignment resource count must be positive",
                ));
            }
            let CandidateSignals: BTreeSet<String> = CandidateValues
                .iter()
                .map(|Value| Value.0.clone())
                .collect();
            let MissingSignals: Vec<String> = RequiredSignals
                .iter()
                .filter(|Signal| !CandidateSignals.contains(*Signal))
                .cloned()
                .collect();
            let Result = if let Some(FailureNet) = MissingSignals.first() {
                RoutingAssignmentResult {
                    Success: false,
                    SelectedCandidateIds: Vec::new(),
                    ExpansionCount,
                    BudgetExhausted: false,
                    DeadlineExceeded: false,
                    CompletedWork: ExpansionCount,
                    FailureNet: Some(FailureNet.clone()),
                    ConflictSignals: MissingSignals,
                    ConflictResourceIndices: Vec::new(),
                    PairwiseIncompatibleSignals: Vec::new(),
                    PairwiseCompatibilityComplete: true,
                }
            } else if BaseValues.is_empty() {
                PlanAuthoritativeRoutesWithInitialExpansionAndDeadline(
                    CandidateValues,
                    ResourceCount,
                    ExpansionCount,
                    EffectiveMaximumExpansionCount,
                    Deadline.clone(),
                )?
            } else {
                PlanAuthoritativeRoutesWithBaseAndInitialExpansionAndDeadline(
                    CandidateValues,
                    BaseValues,
                    ResourceCount,
                    ExpansionCount,
                    EffectiveMaximumExpansionCount,
                    Deadline.clone(),
                )?
            };
            let InitialMemberExpansionCount = ExpansionCount;
            ExpansionCount = ExpansionCount
                .max(Result.ExpansionCount)
                .min(EffectiveMaximumExpansionCount);
            AttemptFailureNets.push((TemplateId.clone(), Result.FailureNet.clone()));
            AttemptExpansionCounts.push((
                TemplateId.clone(),
                ExpansionCount.saturating_sub(InitialMemberExpansionCount),
            ));
            AttemptPartialCandidateIds
                .push((TemplateId.clone(), Result.SelectedCandidateIds.clone()));
            if FirstConflictSignals.is_empty() && !Result.ConflictSignals.is_empty() {
                FirstConflictSignals = Result.ConflictSignals.clone();
                FirstConflictResourceIndices = Result.ConflictResourceIndices.clone();
            }
            if FirstPairwiseIncompatibleSignals.is_empty()
                && !Result.PairwiseIncompatibleSignals.is_empty()
            {
                FirstPairwiseIncompatibleSignals = Result.PairwiseIncompatibleSignals.clone();
            }
            AttemptPairwiseIncompatibleSignals.push((
                TemplateId.clone(),
                Result.PairwiseIncompatibleSignals.clone(),
            ));
            if Result.DeadlineExceeded || Result.BudgetExhausted {
                return Ok(TemplateRoutingAssignmentResult {
                    Status: "Incomplete".to_string(),
                    Success: false,
                    Complete: false,
                    Unsatisfiable: false,
                    IncompleteReason: if Result.DeadlineExceeded {
                        "assignment-deadline".to_string()
                    } else {
                        "assignment-work-cap".to_string()
                    },
                    SelectedTemplateId: None,
                    SelectedTemplateObjective: Vec::new(),
                    SelectedCandidateIds: Vec::new(),
                    ExpansionCount,
                    BudgetExhausted: Result.BudgetExhausted,
                    DeadlineExceeded: Result.DeadlineExceeded,
                    CompletedWork: ExpansionCount,
                    FailureNet: Result.FailureNet,
                    ConflictSignals: FirstConflictSignals,
                    ConflictResourceIndices: FirstConflictResourceIndices,
                    PairwiseIncompatibleSignals: if Result.PairwiseIncompatibleSignals.is_empty() {
                        FirstPairwiseIncompatibleSignals
                    } else {
                        Result.PairwiseIncompatibleSignals
                    },
                    PairwiseCompatibilityComplete: Result.PairwiseCompatibilityComplete,
                    AttemptedTemplateIds,
                    AttemptPairwiseIncompatibleSignals,
                    AttemptFailureNets,
                    AttemptExpansionCounts,
                    AttemptPartialCandidateIds,
                    NonExhaustiveTemplateDomain,
                    CompactMaskTelemetry: Vec::new(),
                });
            }
            if Result.Success {
                SuccessfulMembers.push((TemplateId, TemplateObjective, Result));
            }
            Index += 1;
        }
        if let Some((TemplateId, TemplateObjective, Result)) = SuccessfulMembers
            .into_iter()
            .min_by(|First, Second| First.0.cmp(&Second.0))
        {
            return Ok(TemplateRoutingAssignmentResult {
                Status: "Feasible".to_string(),
                Success: true,
                Complete: true,
                Unsatisfiable: false,
                IncompleteReason: String::new(),
                SelectedTemplateId: Some(TemplateId),
                SelectedTemplateObjective: TemplateObjective,
                SelectedCandidateIds: Result.SelectedCandidateIds,
                ExpansionCount,
                BudgetExhausted: false,
                DeadlineExceeded: false,
                CompletedWork: ExpansionCount,
                FailureNet: None,
                ConflictSignals: Vec::new(),
                ConflictResourceIndices: Vec::new(),
                PairwiseIncompatibleSignals: Vec::new(),
                PairwiseCompatibilityComplete: true,
                AttemptedTemplateIds,
                AttemptPairwiseIncompatibleSignals,
                AttemptFailureNets,
                AttemptExpansionCounts,
                AttemptPartialCandidateIds,
                NonExhaustiveTemplateDomain,
                CompactMaskTelemetry: Vec::new(),
            });
        }
    }

    let Unsatisfiable = !NonExhaustiveTemplateDomain;
    Ok(TemplateRoutingAssignmentResult {
        Status: if Unsatisfiable {
            "Unsatisfiable"
        } else {
            "Incomplete"
        }
        .to_string(),
        Success: false,
        Complete: Unsatisfiable,
        Unsatisfiable,
        IncompleteReason: if Unsatisfiable {
            "complete-capacity-core".to_string()
        } else {
            "non-exhaustive-template-domain".to_string()
        },
        SelectedTemplateId: None,
        SelectedTemplateObjective: Vec::new(),
        SelectedCandidateIds: Vec::new(),
        ExpansionCount,
        BudgetExhausted: false,
        DeadlineExceeded: false,
        CompletedWork: ExpansionCount,
        FailureNet: None,
        ConflictSignals: FirstConflictSignals,
        ConflictResourceIndices: FirstConflictResourceIndices,
        PairwiseIncompatibleSignals: FirstPairwiseIncompatibleSignals,
        PairwiseCompatibilityComplete: true,
        AttemptedTemplateIds,
        AttemptPairwiseIncompatibleSignals,
        AttemptFailureNets,
        AttemptExpansionCounts,
        AttemptPartialCandidateIds,
        NonExhaustiveTemplateDomain,
        CompactMaskTelemetry: Vec::new(),
    })
}

fn ValidateCompactPrimitiveValues(
    PrimitiveValues: Vec<CompactClaimPrimitiveValue>,
    ResourcePositions: &[Position],
    Deadline: &RuntimeDeadline,
) -> PyResult<Option<Vec<CompactClaimPrimitiveValue>>> {
    let ResourceSet = ResourcePositions.iter().copied().collect::<BTreeSet<_>>();
    if ResourceSet.len() != ResourcePositions.len() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "compact resource vocabulary contains duplicates",
        ));
    }
    for (Index, (_MaskReuseFingerprint, Wire, Support, Air, Electrical, DeferredGuide)) in
        PrimitiveValues.iter().enumerate()
    {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Ok(None);
        }
        if !DeferredGuide.is_empty()
            && (!Wire.is_empty() || !Support.is_empty() || !Electrical.is_empty())
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "deferred compact route primitive also contains expanded claims",
            ));
        }
        if Air.iter().any(|Value| !ResourceSet.contains(Value))
            || (DeferredGuide.is_empty()
                && Wire
                    .iter()
                    .chain(Support)
                    .chain(Electrical)
                    .any(|Value| !ResourceSet.contains(Value)))
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "compact primitive references a resource outside the vocabulary",
            ));
        }
    }
    Ok(Some(PrimitiveValues))
}

fn SameCompactClaimPrimitivePayload(
    First: &CompactClaimPrimitiveValue,
    Second: &CompactClaimPrimitiveValue,
) -> bool {
    let (_, FirstWire, FirstSupport, FirstAir, FirstElectrical, FirstDeferred) = First;
    let (_, SecondWire, SecondSupport, SecondAir, SecondElectrical, SecondDeferred) = Second;
    [
        (FirstWire, SecondWire),
        (FirstSupport, SecondSupport),
        (FirstAir, SecondAir),
        (FirstElectrical, SecondElectrical),
        (FirstDeferred, SecondDeferred),
    ]
    .into_iter()
    .all(|(FirstValues, SecondValues)| {
        FirstValues.iter().copied().collect::<BTreeSet<_>>()
            == SecondValues.iter().copied().collect::<BTreeSet<_>>()
    })
}

#[derive(Default)]
struct CompactMaskBuildTelemetry {
    PrimitiveCacheHits: usize,
    PrimitiveCacheMisses: usize,
    FactorCacheHits: usize,
    FactorCacheMisses: usize,
    ElapsedMilliseconds: usize,
}

impl CompactMaskBuildTelemetry {
    fn ToValues(&self, SolveElapsedMilliseconds: usize) -> Vec<(String, usize)> {
        vec![
            ("PrimitiveCacheHits".to_string(), self.PrimitiveCacheHits),
            (
                "PrimitiveCacheMisses".to_string(),
                self.PrimitiveCacheMisses,
            ),
            ("FactorCacheHits".to_string(), self.FactorCacheHits),
            ("FactorCacheMisses".to_string(), self.FactorCacheMisses),
            ("ElapsedMilliseconds".to_string(), self.ElapsedMilliseconds),
            (
                "SolveElapsedMilliseconds".to_string(),
                SolveElapsedMilliseconds,
            ),
        ]
    }
}

fn BuildCompactFactorGroups(
    Values: Vec<CompactFactorValue>,
    PrimitiveValues: &[CompactClaimPrimitiveValue],
    GlobalIndexByPosition: &mut HashMap<Position, usize>,
    PrimitiveMasks: &mut BTreeMap<usize, Arc<ClaimMask>>,
    PhysicalMasksByFingerprint: &mut HashMap<String, Vec<(usize, Arc<ClaimMask>)>>,
    FactorMasksByFingerprint: &mut HashMap<String, (Vec<String>, Arc<ClaimMask>)>,
    Telemetry: &mut CompactMaskBuildTelemetry,
    Deadline: &RuntimeDeadline,
) -> PyResult<Option<(BTreeMap<String, Vec<AssignmentCandidate>>, usize)>> {
    let ReferencedPrimitiveIds = Values
        .iter()
        .flat_map(|Value| Value.3.iter().copied())
        .collect::<BTreeSet<_>>();
    for PrimitiveId in &ReferencedPrimitiveIds {
        if PrimitiveMasks.contains_key(PrimitiveId) {
            Telemetry.PrimitiveCacheHits += 1;
            continue;
        }
        let Some(PrimitiveValue) = PrimitiveValues.get(*PrimitiveId) else {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "compact factor references an unknown primitive",
            ));
        };
        let (MaskReuseFingerprint, Wire, Support, Air, Electrical, DeferredGuide) = PrimitiveValue;
        if !MaskReuseFingerprint.is_empty() {
            if let Some(ExistingValues) = PhysicalMasksByFingerprint.get(MaskReuseFingerprint) {
                if let Some((_ExistingId, ExistingMask)) =
                    ExistingValues.iter().find(|(ExistingId, _Mask)| {
                        SameCompactClaimPrimitivePayload(
                            &PrimitiveValues[*ExistingId],
                            PrimitiveValue,
                        )
                    })
                {
                    PrimitiveMasks.insert(*PrimitiveId, Arc::clone(ExistingMask));
                    Telemetry.PrimitiveCacheHits += 1;
                    continue;
                }
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "compact mask reuse fingerprint collision: key={} existing={} current={}",
                    MaskReuseFingerprint, ExistingValues[0].0, PrimitiveId,
                )));
            }
        }
        let (Wire, Support, Air, Electrical) = if DeferredGuide.is_empty() {
            (
                Wire.clone(),
                Support.clone(),
                Air.clone(),
                Electrical.clone(),
            )
        } else {
            let Wire = DeferredGuide.clone();
            let Support = Wire
                .iter()
                .map(|(X, Y, Z)| (*X, Y.saturating_sub(1), *Z))
                .collect::<Vec<_>>();
            let mut Electrical = Wire.clone();
            for (X, Y, Z) in &Wire {
                Electrical.extend([
                    (X.saturating_add(1), *Y, *Z),
                    (X.saturating_sub(1), *Y, *Z),
                    (*X, *Y, Z.saturating_add(1)),
                    (*X, *Y, Z.saturating_sub(1)),
                    (X.saturating_add(1), Y.saturating_add(1), *Z),
                    (X.saturating_add(1), Y.saturating_sub(1), *Z),
                    (X.saturating_sub(1), Y.saturating_add(1), *Z),
                    (X.saturating_sub(1), Y.saturating_sub(1), *Z),
                    (*X, Y.saturating_add(1), Z.saturating_add(1)),
                    (*X, Y.saturating_sub(1), Z.saturating_add(1)),
                    (*X, Y.saturating_add(1), Z.saturating_sub(1)),
                    (*X, Y.saturating_sub(1), Z.saturating_sub(1)),
                ]);
            }
            (Wire, Support, Air.clone(), Electrical)
        };
        let mut Remap = |Values: &[Position]| -> Vec<usize> {
            Values
                .iter()
                .map(|Value| {
                    if let Some(Index) = GlobalIndexByPosition.get(Value) {
                        *Index
                    } else {
                        let Index = GlobalIndexByPosition.len();
                        GlobalIndexByPosition.insert(*Value, Index);
                        Index
                    }
                })
                .collect()
        };
        let Wire = Remap(&Wire);
        let Support = Remap(&Support);
        let Air = Remap(&Air);
        let Electrical = Remap(&Electrical);
        let Primitive = match ClaimMask::FromIndicesWithDeadline(
            GlobalIndexByPosition.len().max(1),
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
                    "compact primitive references a resource outside the vocabulary",
                ));
            }
        };
        PrimitiveMasks.insert(*PrimitiveId, Arc::clone(&Primitive));
        Telemetry.PrimitiveCacheMisses += 1;
        if !MaskReuseFingerprint.is_empty() {
            PhysicalMasksByFingerprint
                .entry(MaskReuseFingerprint.clone())
                .or_default()
                .push((*PrimitiveId, Primitive));
        }
    }
    let ResourceCount = GlobalIndexByPosition.len().max(1);
    let mut Groups: BTreeMap<String, Vec<AssignmentCandidate>> = BTreeMap::new();
    let mut Identities = BTreeSet::new();
    for (
        Index,
        (
            Signal,
            CandidateId,
            FactorMaskReuseFingerprint,
            PrimitiveIds,
            MaterialCost,
            FootprintGrowth,
            Length,
            BendCount,
            ViaCount,
            ContractRequirements,
            OwnerSignal,
        ),
    ) in Values.into_iter().enumerate()
    {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Ok(None);
        }
        if Signal.is_empty()
            || CandidateId.is_empty()
            || OwnerSignal.is_empty()
            || !Identities.insert((Signal.clone(), CandidateId.clone()))
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "compact factor identities must be nonempty and unique",
            ));
        }
        let mut SeenPrimitiveIds = BTreeSet::new();
        let mut PrimitiveMaskReuseFingerprints = Vec::new();
        for PrimitiveId in &PrimitiveIds {
            if !SeenPrimitiveIds.insert(*PrimitiveId) {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "compact factor repeats a primitive reference",
                ));
            }
            if !PrimitiveMasks.contains_key(PrimitiveId) {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "compact factor references an unknown primitive",
                ));
            }
            PrimitiveMaskReuseFingerprints.push(PrimitiveValues[*PrimitiveId].0.clone());
        }
        let ReusableFactorMask = !FactorMaskReuseFingerprint.is_empty()
            && PrimitiveMaskReuseFingerprints
                .iter()
                .all(|Value| !Value.is_empty());
        let Claims = if ReusableFactorMask {
            if let Some((ExistingPrimitiveFingerprints, ExistingMask)) =
                FactorMasksByFingerprint.get(&FactorMaskReuseFingerprint)
            {
                if *ExistingPrimitiveFingerprints != PrimitiveMaskReuseFingerprints {
                    return Err(pyo3::exceptions::PyValueError::new_err(
                        "compact factor mask reuse fingerprint collision",
                    ));
                }
                Telemetry.FactorCacheHits += 1;
                Arc::clone(ExistingMask)
            } else {
                let Some(mut Claims) = ClaimMask::NewWithDeadline(ResourceCount, Deadline) else {
                    return Ok(None);
                };
                for PrimitiveId in PrimitiveIds {
                    if !Claims.UnionWithDeadline(&PrimitiveMasks[&PrimitiveId], Deadline) {
                        return Ok(None);
                    }
                }
                let Claims = Arc::new(Claims);
                FactorMasksByFingerprint.insert(
                    FactorMaskReuseFingerprint,
                    (PrimitiveMaskReuseFingerprints, Arc::clone(&Claims)),
                );
                Telemetry.FactorCacheMisses += 1;
                Claims
            }
        } else {
            let Some(mut Claims) = ClaimMask::NewWithDeadline(ResourceCount, Deadline) else {
                return Ok(None);
            };
            for PrimitiveId in PrimitiveIds {
                if !Claims.UnionWithDeadline(&PrimitiveMasks[&PrimitiveId], Deadline) {
                    return Ok(None);
                }
            }
            Telemetry.FactorCacheMisses += 1;
            Arc::new(Claims)
        };
        Groups.entry(Signal).or_default().push(AssignmentCandidate {
            CandidateId,
            TemplateRequirements: ParseContractRequirements(&ContractRequirements),
            ForbiddenCandidateIds: Arc::new(Vec::new()),
            OrderedWire: Arc::new(Vec::new()),
            PoweredAccessConstraint: None,
            OwnerSignal,
            Claims,
            MaterialCost,
            FootprintGrowth,
            Length,
            BendCount,
            ViaCount,
        });
    }
    Ok(Some((Groups, ResourceCount)))
}

/// Select one compact portfolio while constructing each candidate mask only
/// when its member is attempted.
pub(crate) fn SolveCompactTemplateFactorCatalogWithDeadline(
    ResourcePositions: Vec<Position>,
    PrimitiveValues: Vec<CompactClaimPrimitiveValue>,
    FactorValues: Vec<CompactFactorValue>,
    mut Members: Vec<CompactFactorMemberValue>,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
    NonExhaustiveTemplateDomain: bool,
) -> PyResult<TemplateRoutingAssignmentResult> {
    let SolveStartedAt = Instant::now();
    let Some(PrimitiveValues) =
        ValidateCompactPrimitiveValues(PrimitiveValues, &ResourcePositions, &Deadline)?
    else {
        return Ok(TemplateRoutingAssignmentResult {
            Status: "Incomplete".to_string(),
            Success: false,
            Complete: false,
            Unsatisfiable: false,
            IncompleteReason: "assignment-deadline".to_string(),
            SelectedTemplateId: None,
            SelectedTemplateObjective: Vec::new(),
            SelectedCandidateIds: Vec::new(),
            ExpansionCount: 0,
            BudgetExhausted: false,
            DeadlineExceeded: true,
            CompletedWork: 0,
            FailureNet: None,
            ConflictSignals: Vec::new(),
            ConflictResourceIndices: Vec::new(),
            PairwiseIncompatibleSignals: Vec::new(),
            PairwiseCompatibilityComplete: false,
            AttemptedTemplateIds: Vec::new(),
            AttemptPairwiseIncompatibleSignals: Vec::new(),
            AttemptFailureNets: Vec::new(),
            AttemptExpansionCounts: Vec::new(),
            AttemptPartialCandidateIds: Vec::new(),
            NonExhaustiveTemplateDomain,
            CompactMaskTelemetry: Vec::new(),
        });
    };
    let mut GlobalIndexByPosition = ResourcePositions
        .iter()
        .copied()
        .enumerate()
        .map(|(Index, PositionValue)| (PositionValue, Index))
        .collect::<HashMap<_, _>>();
    let mut PrimitiveMasks: BTreeMap<usize, Arc<ClaimMask>> = BTreeMap::new();
    let mut PhysicalMasksByFingerprint: HashMap<String, Vec<(usize, Arc<ClaimMask>)>> =
        HashMap::new();
    let mut FactorMasksByFingerprint: HashMap<String, (Vec<String>, Arc<ClaimMask>)> =
        HashMap::new();
    let mut CompactMaskTelemetry = CompactMaskBuildTelemetry::default();
    Members.sort_by(|First, Second| First.1.cmp(&Second.1).then_with(|| First.0.cmp(&Second.0)));
    let MemberIds = Members
        .iter()
        .map(|Value| Value.0.clone())
        .collect::<Vec<_>>();
    if MemberIds.iter().any(|Value| Value.is_empty())
        || MemberIds.iter().collect::<BTreeSet<_>>().len() != MemberIds.len()
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "compact catalog member ids must be nonempty and unique",
        ));
    }
    let EffectiveMaximumExpansionCount = MaximumExpansionCount.clamp(1, MAXIMUM_EXPANSIONS);
    let mut ExpansionCount = 0usize;
    let mut AttemptedTemplateIds = Vec::new();
    let mut FirstConflictSignals = Vec::new();
    let mut FirstConflictResourceIndices = Vec::new();
    let mut FirstPairwiseIncompatibleSignals = Vec::new();
    let mut AttemptPairwiseIncompatibleSignals = Vec::new();
    let mut AttemptFailureNets = Vec::new();
    let mut AttemptExpansionCounts = Vec::new();
    let mut AttemptPartialCandidateIds = Vec::new();
    let mut Index = 0usize;
    while Index < Members.len() {
        if Deadline.Check() {
            return Ok(TemplateRoutingAssignmentResult {
                Status: "Incomplete".to_string(),
                Success: false,
                Complete: false,
                Unsatisfiable: false,
                IncompleteReason: "assignment-deadline".to_string(),
                SelectedTemplateId: None,
                SelectedTemplateObjective: Vec::new(),
                SelectedCandidateIds: Vec::new(),
                ExpansionCount,
                BudgetExhausted: false,
                DeadlineExceeded: true,
                CompletedWork: ExpansionCount,
                FailureNet: None,
                ConflictSignals: FirstConflictSignals,
                ConflictResourceIndices: FirstConflictResourceIndices,
                PairwiseIncompatibleSignals: FirstPairwiseIncompatibleSignals,
                PairwiseCompatibilityComplete: false,
                AttemptedTemplateIds,
                AttemptPairwiseIncompatibleSignals,
                AttemptFailureNets,
                AttemptExpansionCounts,
                AttemptPartialCandidateIds,
                NonExhaustiveTemplateDomain,
                CompactMaskTelemetry: CompactMaskTelemetry
                    .ToValues(SolveStartedAt.elapsed().as_millis() as usize),
            });
        }
        let Objective = Members[Index].1.clone();
        while Index < Members.len() && Members[Index].1 == Objective {
            let (TemplateId, TemplateObjective, RequiredSignals, FactorIndexes) =
                Members[Index].clone();
            AttemptedTemplateIds.push(TemplateId.clone());
            let MemberFactorValues = FactorIndexes
                .iter()
                .map(|FactorIndex| {
                    FactorValues.get(*FactorIndex).cloned().ok_or_else(|| {
                        pyo3::exceptions::PyValueError::new_err(
                            "compact member references an unknown factor",
                        )
                    })
                })
                .collect::<PyResult<Vec<_>>>()?;
            let CandidateSignals = MemberFactorValues
                .iter()
                .map(|Value| Value.0.clone())
                .collect::<BTreeSet<_>>();
            let MissingSignals = RequiredSignals
                .iter()
                .filter(|Signal| !CandidateSignals.contains(*Signal))
                .cloned()
                .collect::<Vec<_>>();
            let Result = if let Some(FailureNet) = MissingSignals.first() {
                RoutingAssignmentResult {
                    Success: false,
                    SelectedCandidateIds: Vec::new(),
                    ExpansionCount,
                    BudgetExhausted: false,
                    DeadlineExceeded: false,
                    CompletedWork: ExpansionCount,
                    FailureNet: Some(FailureNet.clone()),
                    ConflictSignals: MissingSignals,
                    ConflictResourceIndices: Vec::new(),
                    PairwiseIncompatibleSignals: Vec::new(),
                    PairwiseCompatibilityComplete: true,
                }
            } else {
                let MaskBuildStartedAt = Instant::now();
                let BuiltGroups = BuildCompactFactorGroups(
                    MemberFactorValues,
                    &PrimitiveValues,
                    &mut GlobalIndexByPosition,
                    &mut PrimitiveMasks,
                    &mut PhysicalMasksByFingerprint,
                    &mut FactorMasksByFingerprint,
                    &mut CompactMaskTelemetry,
                    &Deadline,
                )?;
                CompactMaskTelemetry.ElapsedMilliseconds = CompactMaskTelemetry
                    .ElapsedMilliseconds
                    .saturating_add(MaskBuildStartedAt.elapsed().as_millis() as usize);
                let Some((mut Groups, MemberResourceCount)) = BuiltGroups else {
                    return Ok(TemplateRoutingAssignmentResult {
                        Status: "Incomplete".to_string(),
                        Success: false,
                        Complete: false,
                        Unsatisfiable: false,
                        IncompleteReason: "assignment-deadline".to_string(),
                        SelectedTemplateId: None,
                        SelectedTemplateObjective: Vec::new(),
                        SelectedCandidateIds: Vec::new(),
                        ExpansionCount,
                        BudgetExhausted: false,
                        DeadlineExceeded: true,
                        CompletedWork: ExpansionCount,
                        FailureNet: None,
                        ConflictSignals: FirstConflictSignals,
                        ConflictResourceIndices: FirstConflictResourceIndices,
                        PairwiseIncompatibleSignals: FirstPairwiseIncompatibleSignals,
                        PairwiseCompatibilityComplete: false,
                        AttemptedTemplateIds,
                        AttemptPairwiseIncompatibleSignals,
                        AttemptFailureNets,
                        AttemptExpansionCounts,
                        AttemptPartialCandidateIds,
                        NonExhaustiveTemplateDomain,
                        CompactMaskTelemetry: CompactMaskTelemetry
                            .ToValues(SolveStartedAt.elapsed().as_millis() as usize),
                    });
                };
                let AccessOnlyFactorDomain = Groups
                    .keys()
                    .all(|Signal| Signal.starts_with("__access_terminal__:"));
                PlanAuthoritativeCandidateGroupsWithInitialExpansionAndDeadline(
                    &mut Groups,
                    MemberResourceCount,
                    ExpansionCount,
                    EffectiveMaximumExpansionCount,
                    Deadline.clone(),
                    AccessOnlyFactorDomain,
                    true,
                    None,
                )?
            };
            let InitialMemberExpansionCount = ExpansionCount;
            ExpansionCount = ExpansionCount
                .max(Result.ExpansionCount)
                .min(EffectiveMaximumExpansionCount);
            AttemptFailureNets.push((TemplateId.clone(), Result.FailureNet.clone()));
            AttemptExpansionCounts.push((
                TemplateId.clone(),
                ExpansionCount.saturating_sub(InitialMemberExpansionCount),
            ));
            AttemptPartialCandidateIds
                .push((TemplateId.clone(), Result.SelectedCandidateIds.clone()));
            if FirstConflictSignals.is_empty() && !Result.ConflictSignals.is_empty() {
                FirstConflictSignals = Result.ConflictSignals.clone();
                FirstConflictResourceIndices = Result.ConflictResourceIndices.clone();
            }
            if FirstPairwiseIncompatibleSignals.is_empty()
                && !Result.PairwiseIncompatibleSignals.is_empty()
            {
                FirstPairwiseIncompatibleSignals = Result.PairwiseIncompatibleSignals.clone();
            }
            AttemptPairwiseIncompatibleSignals.push((
                TemplateId.clone(),
                Result.PairwiseIncompatibleSignals.clone(),
            ));
            if Result.DeadlineExceeded || Result.BudgetExhausted {
                return Ok(TemplateRoutingAssignmentResult {
                    Status: "Incomplete".to_string(),
                    Success: false,
                    Complete: false,
                    Unsatisfiable: false,
                    IncompleteReason: if Result.DeadlineExceeded {
                        "assignment-deadline".to_string()
                    } else {
                        "assignment-work-cap".to_string()
                    },
                    SelectedTemplateId: None,
                    SelectedTemplateObjective: Vec::new(),
                    SelectedCandidateIds: Vec::new(),
                    ExpansionCount,
                    BudgetExhausted: Result.BudgetExhausted,
                    DeadlineExceeded: Result.DeadlineExceeded,
                    CompletedWork: ExpansionCount,
                    FailureNet: Result.FailureNet,
                    ConflictSignals: FirstConflictSignals,
                    ConflictResourceIndices: FirstConflictResourceIndices,
                    PairwiseIncompatibleSignals: if Result.PairwiseIncompatibleSignals.is_empty() {
                        FirstPairwiseIncompatibleSignals
                    } else {
                        Result.PairwiseIncompatibleSignals
                    },
                    PairwiseCompatibilityComplete: Result.PairwiseCompatibilityComplete,
                    AttemptedTemplateIds,
                    AttemptPairwiseIncompatibleSignals,
                    AttemptFailureNets,
                    AttemptExpansionCounts,
                    AttemptPartialCandidateIds,
                    NonExhaustiveTemplateDomain,
                    CompactMaskTelemetry: CompactMaskTelemetry
                        .ToValues(SolveStartedAt.elapsed().as_millis() as usize),
                });
            }
            if Result.Success {
                return Ok(TemplateRoutingAssignmentResult {
                    Status: "Feasible".to_string(),
                    Success: true,
                    Complete: true,
                    Unsatisfiable: false,
                    IncompleteReason: String::new(),
                    SelectedTemplateId: Some(TemplateId),
                    SelectedTemplateObjective: TemplateObjective,
                    SelectedCandidateIds: Result.SelectedCandidateIds,
                    ExpansionCount,
                    BudgetExhausted: false,
                    DeadlineExceeded: false,
                    CompletedWork: ExpansionCount,
                    FailureNet: None,
                    ConflictSignals: Vec::new(),
                    ConflictResourceIndices: Vec::new(),
                    PairwiseIncompatibleSignals: Vec::new(),
                    PairwiseCompatibilityComplete: true,
                    AttemptedTemplateIds,
                    AttemptPairwiseIncompatibleSignals,
                    AttemptFailureNets,
                    AttemptExpansionCounts,
                    AttemptPartialCandidateIds,
                    NonExhaustiveTemplateDomain,
                    CompactMaskTelemetry: CompactMaskTelemetry
                        .ToValues(SolveStartedAt.elapsed().as_millis() as usize),
                });
            }
            Index += 1;
        }
    }
    let Unsatisfiable = !NonExhaustiveTemplateDomain;
    Ok(TemplateRoutingAssignmentResult {
        Status: if Unsatisfiable {
            "Unsatisfiable"
        } else {
            "Incomplete"
        }
        .to_string(),
        Success: false,
        Complete: Unsatisfiable,
        Unsatisfiable,
        IncompleteReason: if Unsatisfiable {
            "complete-capacity-core".to_string()
        } else {
            "non-exhaustive-template-domain".to_string()
        },
        SelectedTemplateId: None,
        SelectedTemplateObjective: Vec::new(),
        SelectedCandidateIds: Vec::new(),
        ExpansionCount,
        BudgetExhausted: false,
        DeadlineExceeded: false,
        CompletedWork: ExpansionCount,
        FailureNet: None,
        ConflictSignals: FirstConflictSignals,
        ConflictResourceIndices: FirstConflictResourceIndices,
        PairwiseIncompatibleSignals: FirstPairwiseIncompatibleSignals,
        PairwiseCompatibilityComplete: true,
        AttemptedTemplateIds,
        AttemptPairwiseIncompatibleSignals,
        AttemptFailureNets,
        AttemptExpansionCounts,
        AttemptPartialCandidateIds,
        NonExhaustiveTemplateDomain,
        CompactMaskTelemetry: CompactMaskTelemetry
            .ToValues(SolveStartedAt.elapsed().as_millis() as usize),
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
            String::new(),
            "Signal".to_string(),
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
    fn TemplateDomainsShareOneNativeSelectionAndChooseTheFirstWitness() {
        let Result = SolveTemplateAssignmentDomainsWithDeadline(
            vec![
                (
                    "compact".to_string(),
                    vec![1],
                    4,
                    vec!["Signal".to_string()],
                    Vec::new(),
                    Vec::new(),
                ),
                (
                    "access-separated".to_string(),
                    vec![1],
                    4,
                    vec!["Signal".to_string()],
                    vec![CandidateValue()],
                    Vec::new(),
                ),
            ],
            64,
            RuntimeDeadline::FromMilliseconds(Some(1_000)).unwrap(),
            true,
        )
        .expect("template selection should produce a typed result");

        assert!(Result.Success);
        assert!(Result.Complete);
        assert_eq!(
            Result.SelectedTemplateId,
            Some("access-separated".to_string())
        );
        assert_eq!(
            Result.AttemptedTemplateIds,
            vec!["access-separated".to_string(), "compact".to_string()]
        );
        assert_eq!(Result.SelectedCandidateIds.len(), 1);
    }

    #[test]
    fn NonExhaustiveTemplateCoresStayIncomplete() {
        let Result = SolveTemplateAssignmentDomainsWithDeadline(
            vec![(
                "compact".to_string(),
                vec![1],
                4,
                vec!["Signal".to_string()],
                Vec::new(),
                Vec::new(),
            )],
            64,
            RuntimeDeadline::FromMilliseconds(Some(1_000)).unwrap(),
            true,
        )
        .expect("non-exhaustive failure should be typed");

        assert!(!Result.Success);
        assert!(!Result.Complete);
        assert!(!Result.Unsatisfiable);
        assert_eq!(Result.Status, "Incomplete");
        assert_eq!(Result.IncompleteReason, "non-exhaustive-template-domain");
    }

    #[test]
    fn ConditionalTemplateKeysRequireOneSharedInterfaceChoice() {
        let Candidate = |Signal: &str, CandidateId: &str, TemplateKey: &str| {
            (
                Signal.to_string(),
                CandidateId.to_string(),
                vec![1],
                Vec::new(),
                Vec::new(),
                Vec::new(),
                1,
                1,
                1,
                0,
                0,
                TemplateKey.to_string(),
                Signal.to_string(),
            )
        };
        let Result = PlanAuthoritativeRoutesBoundedNative(
            vec![
                Candidate("A", "A-compact", "compact"),
                Candidate("A", "A-separated", "separated"),
                Candidate("B", "B-separated", "separated"),
            ],
            4,
            64,
            1_000,
        )
        .expect("conditional template selection should return a result");

        assert!(Result.Success);
        assert_eq!(
            Result.SelectedCandidateIds,
            vec![
                ("A".to_string(), "A-separated".to_string()),
                ("B".to_string(), "B-separated".to_string()),
            ]
        );
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
                String::new(),
                Signal.to_string(),
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
                String::new(),
                "A".to_string(),
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
                String::new(),
                "A".to_string(),
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
                String::new(),
                "B".to_string(),
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
                String::new(),
                "B".to_string(),
            ),
        ];
        let Result = PlanAuthoritativeRoutesBoundedNative(CandidateValues, 8, 128, 1_000)
            .expect("the second A candidate provides a complete assignment");

        assert!(Result.Success);
        assert!(Result.ConflictSignals.is_empty());
    }

    #[test]
    fn SyntheticFactorsWithOnePhysicalOwnerMayShareClaims() {
        let Candidate = |Signal: &str, CandidateId: &str| -> AssignmentCandidateValue {
            (
                Signal.to_string(),
                CandidateId.to_string(),
                vec![1],
                Vec::new(),
                Vec::new(),
                vec![1],
                1,
                1,
                1,
                0,
                0,
                String::new(),
                "LogicalSignal".to_string(),
            )
        };
        let Result = PlanAuthoritativeRoutesBoundedNative(
            vec![
                Candidate("LogicalSignal", "guide"),
                Candidate("__access_terminal__:LogicalSignal:root", "stub"),
            ],
            2,
            16,
            1_000,
        )
        .expect("same-owner factors should produce one coherent witness");

        assert!(Result.Success);
        assert_eq!(
            Result.SelectedCandidateIds,
            vec![
                ("LogicalSignal".to_string(), "guide".to_string(),),
                (
                    "__access_terminal__:LogicalSignal:root".to_string(),
                    "stub".to_string(),
                ),
            ],
        );
    }

    #[test]
    fn SyntheticFactorsWithOneOwnerRejectStaticSupportWireContradictions() {
        let CandidateValues = vec![
            (
                "factor-a".to_string(),
                "support".to_string(),
                Vec::new(),
                vec![1],
                Vec::new(),
                Vec::new(),
                1,
                1,
                1,
                0,
                0,
                String::new(),
                "LogicalSignal".to_string(),
            ),
            (
                "factor-b".to_string(),
                "conflicting-wire".to_string(),
                vec![1],
                Vec::new(),
                Vec::new(),
                Vec::new(),
                1,
                1,
                1,
                0,
                0,
                String::new(),
                "LogicalSignal".to_string(),
            ),
            (
                "factor-b".to_string(),
                "clear-wire".to_string(),
                vec![2],
                Vec::new(),
                Vec::new(),
                Vec::new(),
                2,
                1,
                1,
                0,
                0,
                String::new(),
                "LogicalSignal".to_string(),
            ),
        ];
        let Result = PlanAuthoritativeRoutesBoundedNative(CandidateValues, 3, 32, 1_000)
            .expect("same-owner static no-good should retain the clear value");

        assert!(Result.Success);
        assert_eq!(
            Result.SelectedCandidateIds,
            vec![
                ("factor-a".to_string(), "support".to_string()),
                ("factor-b".to_string(), "clear-wire".to_string()),
            ],
        );
    }

    fn CompactFactor(
        Variable: &str,
        FactorId: &str,
        PrimitiveIndex: usize,
        Contract: &str,
        Owner: &str,
    ) -> CompactFactorValue {
        (
            Variable.to_string(),
            FactorId.to_string(),
            String::new(),
            vec![PrimitiveIndex],
            1,
            1,
            1,
            0,
            0,
            Contract.to_string(),
            Owner.to_string(),
        )
    }

    #[test]
    fn CompactCatalogSkipsAConflictingL1AndSelectsL2() {
        let Primitives = vec![
            (
                String::new(),
                vec![(0, 0, 0)],
                Vec::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
            ),
            (
                String::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
                vec![(0, 0, 0)],
                Vec::new(),
            ),
            (
                String::new(),
                vec![(1, 0, 0)],
                Vec::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
            ),
            (
                String::new(),
                vec![(2, 0, 0)],
                Vec::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
            ),
        ];
        let Factors = vec![
            CompactFactor("A", "a-l2", 2, "member=l2", "A"),
            CompactFactor("B", "b-l2", 3, "member=l2", "B"),
            CompactFactor("A", "a-l1", 0, "member=l1", "A"),
            CompactFactor("B", "b-l1", 1, "member=l1", "B"),
        ];
        let Members = vec![
            (
                "l2".to_string(),
                vec![2],
                vec!["A".to_string(), "B".to_string()],
                vec![0, 1],
            ),
            (
                "l1".to_string(),
                vec![1],
                vec!["A".to_string(), "B".to_string()],
                vec![2, 3],
            ),
        ];

        let Result = SolveCompactTemplateFactorCatalogWithDeadline(
            vec![(0, 0, 0), (1, 0, 0), (2, 0, 0)],
            Primitives,
            Factors,
            Members,
            64,
            RuntimeDeadline::FromMilliseconds(Some(1_000)).unwrap(),
            true,
        )
        .expect("the compact catalog should produce a typed witness");

        assert!(Result.Success);
        assert!(Result.Complete);
        assert!(!Result.Unsatisfiable);
        assert_eq!(Result.SelectedTemplateId, Some("l2".to_string()));
        assert_eq!(
            Result.SelectedCandidateIds,
            vec![
                ("A".to_string(), "a-l2".to_string()),
                ("B".to_string(), "b-l2".to_string()),
            ],
        );
        assert_eq!(
            Result.AttemptedTemplateIds,
            vec!["l1".to_string(), "l2".to_string()],
        );
    }

    #[test]
    fn CompactCatalogAllowsSameOwnerAccessAndGuideOverlap() {
        let Result = SolveCompactTemplateFactorCatalogWithDeadline(
            vec![(0, 0, 0)],
            vec![(
                String::new(),
                vec![(0, 0, 0)],
                Vec::new(),
                Vec::new(),
                vec![(0, 0, 0)],
                Vec::new(),
            )],
            vec![
                CompactFactor("A", "guide", 0, "access-stub:A=0;member=member", "A"),
                CompactFactor(
                    "__access_terminal__:A",
                    "stub",
                    0,
                    "access-stub:A=0;member=member",
                    "A",
                ),
            ],
            vec![(
                "member".to_string(),
                vec![1],
                vec!["A".to_string(), "__access_terminal__:A".to_string()],
                vec![0, 1],
            )],
            16,
            RuntimeDeadline::FromMilliseconds(Some(1_000)).unwrap(),
            true,
        )
        .expect("same-owner compact factors should be legal");

        assert!(Result.Success);
        assert_eq!(Result.SelectedTemplateId, Some("member".to_string()));
    }

    #[test]
    fn CompactNonExhaustiveFailureIsIncompleteAndWorkCapIsShared() {
        let ConflictFactors = vec![
            CompactFactor("A", "a", 0, "member=conflict", "A"),
            CompactFactor("B", "b", 1, "member=conflict", "B"),
        ];
        let Member = (
            "conflict".to_string(),
            vec![1],
            vec!["A".to_string(), "B".to_string()],
            vec![0, 1],
        );
        let Primitives = vec![
            (
                String::new(),
                vec![(0, 0, 0)],
                Vec::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
            ),
            (
                String::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
                vec![(0, 0, 0)],
                Vec::new(),
            ),
            (
                String::new(),
                vec![(1, 0, 0)],
                Vec::new(),
                Vec::new(),
                Vec::new(),
                Vec::new(),
            ),
        ];
        let CompleteFailure = SolveCompactTemplateFactorCatalogWithDeadline(
            vec![(0, 0, 0), (1, 0, 0)],
            Primitives.clone(),
            ConflictFactors,
            vec![Member.clone()],
            16,
            RuntimeDeadline::FromMilliseconds(Some(1_000)).unwrap(),
            true,
        )
        .expect("a non-exhaustive failure should be typed");
        assert!(!CompleteFailure.Success);
        assert!(!CompleteFailure.Complete);
        assert!(!CompleteFailure.Unsatisfiable);
        assert_eq!(
            CompleteFailure.IncompleteReason,
            "non-exhaustive-template-domain"
        );

        let WorkLimited = SolveCompactTemplateFactorCatalogWithDeadline(
            vec![(0, 0, 0), (1, 0, 0)],
            Primitives,
            vec![
                CompactFactor("A", "a", 0, "member=feasible", "A"),
                CompactFactor("B", "b", 2, "member=feasible", "B"),
            ],
            vec![(
                "feasible".to_string(),
                vec![1],
                vec!["A".to_string(), "B".to_string()],
                vec![0, 1],
            )],
            1,
            RuntimeDeadline::FromMilliseconds(Some(1_000)).unwrap(),
            true,
        )
        .expect("the shared work cap should return incomplete");
        assert!(!WorkLimited.Success);
        assert!(!WorkLimited.Complete);
        assert!(!WorkLimited.Unsatisfiable);
        assert_eq!(WorkLimited.IncompleteReason, "assignment-work-cap");
        assert_eq!(WorkLimited.ExpansionCount, 1);
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
