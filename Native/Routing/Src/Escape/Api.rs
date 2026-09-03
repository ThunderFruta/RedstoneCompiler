//! Stable crate-facing escape planning API and result conversion.

use crate::Core::Deadline::RuntimeDeadline;
use crate::Core::Models::TemplateRoutingAssignmentResult;
use crate::Core::Runtime::RoutingThreadPool;
use crate::Planning::AssignmentPlanning::PlanAuthoritativeCandidateGroupsWithInitialExpansionAndDeadline;
use pyo3::PyResult;
use rayon::prelude::*;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::sync::Arc;
use std::time::Instant;

use super::Candidates::*;
use super::Catalog::*;
use super::State::*;
use super::Traversal::*;

#[cfg(test)]
use crate::Assignment::ParseContractRequirements;
#[cfg(test)]
use crate::Core::Models::Position;

fn LayeredAccessTemplateResult(
    Status: &str,
    Success: bool,
    Complete: bool,
    IncompleteReason: &str,
    SelectedTemplateId: Option<String>,
    SelectedTemplateObjective: Vec<i64>,
    SelectedCandidateIds: Vec<(String, String)>,
    ExpansionCount: usize,
    BudgetExhausted: bool,
    DeadlineExceeded: bool,
    FailureNet: Option<String>,
    ConflictSignals: Vec<String>,
    ConflictResourceIndices: Vec<usize>,
    PairwiseIncompatibleSignals: Vec<(String, String)>,
    PairwiseCompatibilityComplete: bool,
    AttemptedTemplateIds: Vec<String>,
    AttemptPairwiseIncompatibleSignals: Vec<(String, Vec<(String, String)>)>,
    AttemptFailureNets: Vec<(String, Option<String>)>,
    AttemptExpansionCounts: Vec<(String, usize)>,
    AttemptPartialCandidateIds: Vec<(String, Vec<(String, String)>)>,
    EscapeExpansionCount: usize,
    StartedAt: Instant,
) -> TemplateRoutingAssignmentResult {
    TemplateRoutingAssignmentResult {
        Status: Status.to_string(),
        Success,
        Complete,
        Unsatisfiable: false,
        IncompleteReason: IncompleteReason.to_string(),
        SelectedTemplateId,
        SelectedTemplateObjective,
        SelectedCandidateIds,
        ExpansionCount,
        BudgetExhausted,
        DeadlineExceeded,
        CompletedWork: ExpansionCount,
        FailureNet,
        ConflictSignals,
        ConflictResourceIndices,
        PairwiseIncompatibleSignals,
        PairwiseCompatibilityComplete,
        AttemptedTemplateIds,
        AttemptPairwiseIncompatibleSignals,
        AttemptFailureNets,
        AttemptExpansionCounts,
        AttemptPartialCandidateIds,
        NonExhaustiveTemplateDomain: true,
        CompactMaskTelemetry: vec![
            ("NativeBatchCallCount".to_string(), 1),
            ("EscapeExpansionCount".to_string(), EscapeExpansionCount),
            (
                "ElapsedMilliseconds".to_string(),
                StartedAt.elapsed().as_millis() as usize,
            ),
        ],
    }
}

/// Traverse each exact layer world lazily in deterministic member order,
/// compose exact access claims in Rust, and return the first complete
/// capacity witness.  One deadline and one assignment expansion counter
/// cover the operation; an incomplete earlier member can never be skipped.
pub(crate) fn SolveLayeredAccessEscapeFactorCatalogWithDeadline(
    Graphs: Vec<LayeredAccessEscapeGraphValue>,
    mut Members: Vec<LayeredAccessEscapeMemberValue>,
    BendPenalty: usize,
    MaximumAssignmentExpansionCount: usize,
    Deadline: RuntimeDeadline,
) -> PyResult<LayeredAccessEscapeSelectionResult> {
    let StartedAt = Instant::now();
    if Graphs.is_empty() || Members.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access selection requires graphs and members",
        ));
    }
    if Graphs
        .iter()
        .any(|(GraphId, _Adjacency)| GraphId.is_empty())
        || Graphs
            .iter()
            .map(|(GraphId, _Adjacency)| GraphId)
            .collect::<BTreeSet<_>>()
            .len()
            != Graphs.len()
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access graph ids must be nonempty and unique",
        ));
    }
    Members.sort_by(|First, Second| First.1.cmp(&Second.1).then_with(|| First.0.cmp(&Second.0)));
    if Members.iter().any(|Member| {
        Member.0.is_empty() || Member.2 >= Graphs.len() || Member.3.is_empty() || Member.6 < 1
    }) || Members
        .iter()
        .map(|Member| &Member.0)
        .collect::<BTreeSet<_>>()
        .len()
        != Members.len()
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access members require exact unique identities, graphs, requests, and caps",
        ));
    }
    let EffectiveMaximumExpansionCount = MaximumAssignmentExpansionCount.clamp(1, 1_000_000);
    let mut AssignmentExpansionCount = 0usize;
    let mut EscapeExpansionCount = 0usize;
    let mut AttemptedTemplateIds = Vec::new();
    let mut AttemptPairwiseIncompatibleSignals = Vec::new();
    let mut AttemptFailureNets = Vec::new();
    let mut AttemptExpansionCounts = Vec::new();
    let mut AttemptPartialCandidateIds = Vec::new();
    let mut FirstConflictSignals = Vec::new();
    let mut FirstConflictResourceIndices = Vec::new();
    let mut FirstPairwiseIncompatibleSignals = Vec::new();
    let mut CertifiedFixedConflictByGraph = HashMap::<usize, (String, String)>::new();
    let mut PrefetchedEscapeResults = HashMap::<
        String,
        (
            Vec<EscapeRequest>,
            String,
            Vec<EscapeRequestResult>,
            usize,
            bool,
            bool,
        ),
    >::new();

    for MemberIndex in 0..Members.len() {
        let (
            TemplateId,
            TemplateObjective,
            GraphIndex,
            mut Requests,
            RequestMetadata,
            MaximumY,
            EscapeExpansionLimit,
        ) = Members[MemberIndex].clone();
        if Deadline.Check() {
            let Result = LayeredAccessTemplateResult(
                "Incomplete",
                false,
                false,
                "assignment-deadline",
                None,
                Vec::new(),
                Vec::new(),
                AssignmentExpansionCount,
                false,
                true,
                None,
                FirstConflictSignals,
                FirstConflictResourceIndices,
                FirstPairwiseIncompatibleSignals,
                false,
                AttemptedTemplateIds,
                AttemptPairwiseIncompatibleSignals,
                AttemptFailureNets,
                AttemptExpansionCounts,
                AttemptPartialCandidateIds,
                EscapeExpansionCount,
                StartedAt,
            );
            return Ok((Result, None, EscapeExpansionCount));
        }
        AttemptedTemplateIds.push(TemplateId.clone());
        for Request in &mut Requests {
            Request.2.retain(|Ingress| Ingress.1 <= MaximumY);
        }
        let FixedPrefixCandidates = BuildFixedPrefixAccessCandidates(&Requests, &RequestMetadata);
        if let Some((FirstVariable, SecondVariable)) =
            CertifiedFixedConflictByGraph.get(&GraphIndex)
        {
            let FixedConflictStillApplies = FixedPrefixCandidates
                .get(FirstVariable)
                .zip(FixedPrefixCandidates.get(SecondVariable))
                .is_some_and(|(First, Second)| DeferredAccessCandidatesConflict(First, Second));
            if FixedConflictStillApplies {
                let Conflict = (FirstVariable.clone(), SecondVariable.clone());
                AttemptFailureNets.push((TemplateId.clone(), Some(FirstVariable.clone())));
                AttemptExpansionCounts.push((TemplateId.clone(), 0));
                AttemptPartialCandidateIds.push((TemplateId.clone(), Vec::new()));
                AttemptPairwiseIncompatibleSignals
                    .push((TemplateId.clone(), vec![Conflict.clone()]));
                if FirstPairwiseIncompatibleSignals.is_empty() {
                    FirstPairwiseIncompatibleSignals.push(Conflict);
                }
                continue;
            }
        }
        let (_GraphId, Adjacency) = &Graphs[GraphIndex];
        let (
            EffectiveRequests,
            EscapeStatus,
            EscapeResults,
            MemberEscapeExpansionCount,
            EscapeWorkCapExceeded,
            EscapeDeadlineExceeded,
        ) = if let Some(Prefetched) = PrefetchedEscapeResults.remove(&TemplateId) {
            Prefetched
        } else {
            let Outcome = BuildDerivedEscapeStatePathsWithMaximumYAndDeadline(
                Adjacency.clone(),
                Requests.clone(),
                BendPenalty,
                EscapeExpansionLimit,
                Deadline.clone(),
                Some(MaximumY),
            );
            (
                Requests.clone(),
                Outcome.0,
                Outcome.1,
                Outcome.2,
                Outcome.3,
                Outcome.4,
            )
        };
        Requests = EffectiveRequests;
        EscapeExpansionCount = EscapeExpansionCount.saturating_add(MemberEscapeExpansionCount);
        let SelectedMemberResult = (
            TemplateId.clone(),
            EscapeStatus,
            EscapeResults.clone(),
            MemberEscapeExpansionCount,
            EscapeWorkCapExceeded,
            EscapeDeadlineExceeded,
        );
        if EscapeWorkCapExceeded || EscapeDeadlineExceeded {
            let Result = LayeredAccessTemplateResult(
                "Incomplete",
                false,
                false,
                if EscapeDeadlineExceeded {
                    "assignment-deadline"
                } else {
                    "escape-work-cap"
                },
                None,
                Vec::new(),
                Vec::new(),
                AssignmentExpansionCount,
                EscapeWorkCapExceeded,
                EscapeDeadlineExceeded,
                None,
                FirstConflictSignals,
                FirstConflictResourceIndices,
                FirstPairwiseIncompatibleSignals,
                false,
                AttemptedTemplateIds,
                AttemptPairwiseIncompatibleSignals,
                AttemptFailureNets,
                AttemptExpansionCounts,
                AttemptPartialCandidateIds,
                EscapeExpansionCount,
                StartedAt,
            );
            return Ok((Result, Some(SelectedMemberResult), EscapeExpansionCount));
        }
        let Some((mut Groups, ResourceCount)) = BuildLayeredAccessCandidateGroups(
            &Requests,
            &EscapeResults,
            &RequestMetadata,
            &Deadline,
        )?
        else {
            let Result = LayeredAccessTemplateResult(
                "Incomplete",
                false,
                false,
                "assignment-deadline",
                None,
                Vec::new(),
                Vec::new(),
                AssignmentExpansionCount,
                false,
                true,
                None,
                FirstConflictSignals,
                FirstConflictResourceIndices,
                FirstPairwiseIncompatibleSignals,
                false,
                AttemptedTemplateIds,
                AttemptPairwiseIncompatibleSignals,
                AttemptFailureNets,
                AttemptExpansionCounts,
                AttemptPartialCandidateIds,
                EscapeExpansionCount,
                StartedAt,
            );
            return Ok((Result, Some(SelectedMemberResult), EscapeExpansionCount));
        };
        let InitialExpansionCount = AssignmentExpansionCount;
        let Assignment = PlanAuthoritativeCandidateGroupsWithInitialExpansionAndDeadline(
            &mut Groups,
            ResourceCount,
            AssignmentExpansionCount,
            EffectiveMaximumExpansionCount,
            Deadline.clone(),
            true,
            true,
            None,
        )?;
        AssignmentExpansionCount = Assignment
            .ExpansionCount
            .max(AssignmentExpansionCount)
            .min(EffectiveMaximumExpansionCount);
        AttemptFailureNets.push((TemplateId.clone(), Assignment.FailureNet.clone()));
        AttemptExpansionCounts.push((
            TemplateId.clone(),
            AssignmentExpansionCount.saturating_sub(InitialExpansionCount),
        ));
        AttemptPartialCandidateIds
            .push((TemplateId.clone(), Assignment.SelectedCandidateIds.clone()));
        AttemptPairwiseIncompatibleSignals.push((
            TemplateId.clone(),
            Assignment.PairwiseIncompatibleSignals.clone(),
        ));
        if FirstConflictSignals.is_empty() && !Assignment.ConflictSignals.is_empty() {
            FirstConflictSignals = Assignment.ConflictSignals.clone();
            FirstConflictResourceIndices = Assignment.ConflictResourceIndices.clone();
        }
        if FirstPairwiseIncompatibleSignals.is_empty()
            && !Assignment.PairwiseIncompatibleSignals.is_empty()
        {
            FirstPairwiseIncompatibleSignals = Assignment.PairwiseIncompatibleSignals.clone();
        }
        if Assignment.DeadlineExceeded || Assignment.BudgetExhausted {
            let Result = LayeredAccessTemplateResult(
                "Incomplete",
                false,
                false,
                if Assignment.DeadlineExceeded {
                    "assignment-deadline"
                } else {
                    "assignment-work-cap"
                },
                None,
                Vec::new(),
                Vec::new(),
                AssignmentExpansionCount,
                Assignment.BudgetExhausted,
                Assignment.DeadlineExceeded,
                Assignment.FailureNet,
                FirstConflictSignals,
                FirstConflictResourceIndices,
                FirstPairwiseIncompatibleSignals,
                Assignment.PairwiseCompatibilityComplete,
                AttemptedTemplateIds,
                AttemptPairwiseIncompatibleSignals,
                AttemptFailureNets,
                AttemptExpansionCounts,
                AttemptPartialCandidateIds,
                EscapeExpansionCount,
                StartedAt,
            );
            return Ok((Result, Some(SelectedMemberResult), EscapeExpansionCount));
        }
        if !Assignment.Success {
            if let Some((FirstVariable, SecondVariable)) = Assignment
                .PairwiseIncompatibleSignals
                .iter()
                .find(|(FirstVariable, SecondVariable)| {
                    FixedPrefixCandidates
                        .get(FirstVariable)
                        .zip(FixedPrefixCandidates.get(SecondVariable))
                        .is_some_and(|(First, Second)| {
                            DeferredAccessCandidatesConflict(First, Second)
                        })
                })
            {
                CertifiedFixedConflictByGraph
                    .insert(GraphIndex, (FirstVariable.clone(), SecondVariable.clone()));
            }
            if !CertifiedFixedConflictByGraph.contains_key(&GraphIndex) {
                let FutureMembers = Members
                    .iter()
                    .skip(MemberIndex + 1)
                    .filter(|Member| Member.2 == GraphIndex)
                    .filter(|Member| !PrefetchedEscapeResults.contains_key(&Member.0))
                    .cloned()
                    .collect::<Vec<_>>();
                let Prefetched = RoutingThreadPool().install(|| {
                    FutureMembers
                        .into_par_iter()
                        .map(
                            |(
                                FutureTemplateId,
                                _FutureObjective,
                                FutureGraphIndex,
                                mut FutureRequests,
                                _FutureMetadata,
                                FutureMaximumY,
                                FutureEscapeExpansionLimit,
                            )| {
                                for Request in &mut FutureRequests {
                                    Request.2.retain(|Ingress| Ingress.1 <= FutureMaximumY);
                                }
                                let Outcome = BuildDerivedEscapeStatePathsWithMaximumYAndDeadline(
                                    Graphs[FutureGraphIndex].1.clone(),
                                    FutureRequests.clone(),
                                    BendPenalty,
                                    FutureEscapeExpansionLimit,
                                    Deadline.clone(),
                                    Some(FutureMaximumY),
                                );
                                (
                                    FutureTemplateId,
                                    (
                                        FutureRequests,
                                        Outcome.0,
                                        Outcome.1,
                                        Outcome.2,
                                        Outcome.3,
                                        Outcome.4,
                                    ),
                                )
                            },
                        )
                        .collect::<Vec<_>>()
                });
                PrefetchedEscapeResults.extend(Prefetched);
            }
        }
        if Assignment.Success {
            let Result = LayeredAccessTemplateResult(
                "Feasible",
                true,
                true,
                "",
                Some(TemplateId),
                TemplateObjective,
                Assignment.SelectedCandidateIds,
                AssignmentExpansionCount,
                false,
                false,
                None,
                Vec::new(),
                Vec::new(),
                Vec::new(),
                true,
                AttemptedTemplateIds,
                AttemptPairwiseIncompatibleSignals,
                AttemptFailureNets,
                AttemptExpansionCounts,
                AttemptPartialCandidateIds,
                EscapeExpansionCount,
                StartedAt,
            );
            return Ok((Result, Some(SelectedMemberResult), EscapeExpansionCount));
        }
    }
    let Result = LayeredAccessTemplateResult(
        "Incomplete",
        false,
        false,
        "non-exhaustive-template-domain",
        None,
        Vec::new(),
        Vec::new(),
        AssignmentExpansionCount,
        false,
        false,
        None,
        FirstConflictSignals,
        FirstConflictResourceIndices,
        FirstPairwiseIncompatibleSignals,
        true,
        AttemptedTemplateIds,
        AttemptPairwiseIncompatibleSignals,
        AttemptFailureNets,
        AttemptExpansionCounts,
        AttemptPartialCandidateIds,
        EscapeExpansionCount,
        StartedAt,
    );
    Ok((Result, None, EscapeExpansionCount))
}

/// Traverse every exact layer member once, compose access and canonical guide
/// factors in native memory, and select one coherent member under one shared
/// assignment counter and absolute deadline.  No Python access-stub or guide
/// domain is materialized before this operation returns its selected witness.
pub(crate) fn SolveLayeredAccessGuideFactorCatalogWithDeadline(
    Graphs: Vec<LayeredAccessEscapeGraphValue>,
    mut Members: Vec<LayeredAccessGuideMemberValue>,
    BendPenalty: usize,
    MaximumAssignmentExpansionCount: usize,
    Deadline: RuntimeDeadline,
) -> PyResult<LayeredAccessGuideSelectionResult> {
    let StartedAt = Instant::now();
    if Graphs.is_empty() || Members.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access-guide selection requires graphs and members",
        ));
    }
    Members.sort_by(|First, Second| First.1.cmp(&Second.1).then_with(|| First.0.cmp(&Second.0)));
    if Members.iter().any(|Member| {
        Member.0.is_empty()
            || Member.2 >= Graphs.len()
            || Member.3.is_empty()
            || Member.6 < 1
            || Member.7 .0.is_empty()
            || Member.7 .5 < 1
    }) || Members
        .iter()
        .map(|Member| &Member.0)
        .collect::<BTreeSet<_>>()
        .len()
        != Members.len()
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access-guide members require exact unique complete controls",
        ));
    }
    let mut EscapeExpansionCount = 0usize;
    let mut AssignmentExpansionCount = 0usize;
    let EffectiveMaximumExpansionCount = MaximumAssignmentExpansionCount.clamp(1, 1_000_000);
    let mut AttemptedTemplateIds = Vec::new();
    let mut AttemptPairwiseIncompatibleSignals = Vec::new();
    let mut AttemptFailureNets = Vec::new();
    let mut AttemptExpansionCounts = Vec::new();
    let mut AttemptPartialCandidateIds = Vec::new();
    let mut FirstConflictSignals = Vec::new();
    let mut FirstConflictResourceIndices = Vec::new();
    let mut FirstPairwiseIncompatibleSignals = Vec::new();
    // Member views of one immutable placement graph frequently differ only
    // in interface contract.  Their guide factors still require the exact
    // same connectivity and powered-path proofs.  Keep those proofs scoped
    // to this one native catalog call and intern them by complete physical
    // identity; unrelated graph/ceiling/claim worlds remain independent.
    let SharedAccessRampCache = Arc::new(LayeredGuideAccessRampCache::New());
    let mut MemberStart = 0usize;
    while MemberStart < Members.len() {
        // Members are already in immutable objective/template order. Evaluate
        // one deterministic native frontier at a time, then consume outcomes
        // in that same order. This uses the existing worker pool and shared
        // assignment/deadline bounds; it neither prunes worlds nor permits a
        // later witness to outrank an earlier complete feasible member.
        let FrontierObjective = &Members[MemberStart].1;
        let MemberEnd = Members[MemberStart..]
            .iter()
            .position(|Member| &Member.1 != FrontierObjective)
            .map_or(Members.len(), |Offset| MemberStart + Offset);
        let TierMembers = &Members[MemberStart..MemberEnd];
        let FixedBaseConflictMemberIds = TierMembers
            .iter()
            .filter(|Member| LayeredGuideControlsHaveFixedBaseConflict(&Member.7))
            .map(|Member| Member.0.clone())
            .collect::<HashSet<_>>();
        for (TierMemberOffset, Member) in TierMembers.iter().enumerate() {
            let MemberIndex = MemberStart + TierMemberOffset;
            let EscapeStartedAt = Instant::now();
            let EscapeResult = if FixedBaseConflictMemberIds.contains(&Member.0) {
                (
                    Member.0.clone(),
                    "Complete".to_string(),
                    Vec::new(),
                    0usize,
                    false,
                    false,
                )
            } else {
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE_VERBOSE").is_some() {
                    eprintln!(
                        "native layered member requests member={} requests={:?}",
                        Member.0,
                        Member
                            .3
                            .iter()
                            .map(|Request| (Request.0.as_str(), Request.1, Request.2.as_slice()))
                            .collect::<Vec<_>>(),
                    );
                }
                let (
                    EscapeStatus,
                    mut EscapeResults,
                    MemberEscapeExpansionCount,
                    WorkCapExceeded,
                    DeadlineExceeded,
                ) = BuildLayeredAccessEscapeViewCatalogWithDeadline(
                    Graphs.clone(),
                    vec![(
                        Member.0.clone(),
                        Member.1.clone(),
                        Member.2,
                        Member.3.clone(),
                        Member.4.clone(),
                        Member.5,
                        Member.6,
                    )],
                    BendPenalty,
                    Member.6,
                    Deadline.clone(),
                )?;
                EscapeExpansionCount =
                    EscapeExpansionCount.saturating_add(MemberEscapeExpansionCount);
                EscapeResults.pop().unwrap_or((
                    Member.0.clone(),
                    EscapeStatus,
                    Vec::new(),
                    MemberEscapeExpansionCount,
                    WorkCapExceeded,
                    DeadlineExceeded,
                ))
            };
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                eprintln!(
                    "native layered member escape member={} expansions={} elapsed={:.3}s",
                    Member.0,
                    EscapeResult.3,
                    EscapeStartedAt.elapsed().as_secs_f64(),
                );
            }
            let DomainStartedAt = Instant::now();
            let PreparedDomain = if FixedBaseConflictMemberIds.contains(&Member.0) {
                Some((
                    BTreeMap::from([("__fixed_base_claim_conflict__".to_string(), Vec::new())]),
                    1,
                    HashMap::new(),
                    Arc::new(vec![Vec::new()]),
                ))
            } else if EscapeResult.4 || EscapeResult.5 || EscapeResult.1 != "Complete" {
                None
            } else {
                BuildLayeredAccessGuideCandidateGroups(
                    &Member.3,
                    &EscapeResult.2,
                    &Member.4,
                    &Member.7,
                    &Graphs[Member.2].1,
                    MemberIndex,
                    Member.2,
                    Member.5,
                    true,
                    &SharedAccessRampCache,
                    &Deadline,
                )?
            };
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                let (
                    CacheHits,
                    CacheMisses,
                    KnownPoweredWitnesses,
                    ExhaustivePoweredProofs,
                    CacheEntries,
                ) = SharedAccessRampCache.Counts();
                eprintln!(
                    "native layered member domain member={} elapsed={:.3}s ramp_cache_hits={} misses={} known_powered_witnesses={} exhaustive_powered_proofs={} entries={}",
                    Member.0,
                    DomainStartedAt.elapsed().as_secs_f64(),
                    CacheHits,
                    CacheMisses,
                    KnownPoweredWitnesses,
                    ExhaustivePoweredProofs,
                    CacheEntries,
                );
            }
            AttemptedTemplateIds.push(Member.0.clone());
            if EscapeResult.4 || EscapeResult.5 || EscapeResult.1 != "Complete" {
                let Result = LayeredAccessTemplateResult(
                    "Incomplete",
                    false,
                    false,
                    if EscapeResult.5 {
                        "assignment-deadline"
                    } else {
                        "escape-work-cap"
                    },
                    None,
                    Vec::new(),
                    Vec::new(),
                    AssignmentExpansionCount,
                    EscapeResult.4,
                    EscapeResult.5,
                    None,
                    FirstConflictSignals,
                    FirstConflictResourceIndices,
                    FirstPairwiseIncompatibleSignals,
                    false,
                    AttemptedTemplateIds,
                    AttemptPairwiseIncompatibleSignals,
                    AttemptFailureNets,
                    AttemptExpansionCounts,
                    AttemptPartialCandidateIds,
                    EscapeExpansionCount,
                    StartedAt,
                );
                return Ok((Result, Some(EscapeResult), Vec::new(), EscapeExpansionCount));
            }
            let RemainingMemberExpansionCount =
                EffectiveMaximumExpansionCount.saturating_sub(AssignmentExpansionCount);
            if RemainingMemberExpansionCount == 0 {
                let Result = LayeredAccessTemplateResult(
                    "Incomplete",
                    false,
                    false,
                    "assignment-work-cap",
                    None,
                    Vec::new(),
                    Vec::new(),
                    AssignmentExpansionCount,
                    true,
                    false,
                    None,
                    FirstConflictSignals,
                    FirstConflictResourceIndices,
                    FirstPairwiseIncompatibleSignals,
                    false,
                    AttemptedTemplateIds,
                    AttemptPairwiseIncompatibleSignals,
                    AttemptFailureNets,
                    AttemptExpansionCounts,
                    AttemptPartialCandidateIds,
                    EscapeExpansionCount,
                    StartedAt,
                );
                return Ok((Result, Some(EscapeResult), Vec::new(), EscapeExpansionCount));
            }
            let AssignmentOutcome: PyResult<
                Option<(
                    crate::Core::Models::RoutingAssignmentResult,
                    HashMap<String, SelectedLayeredGuideValue>,
                )>,
            > = (|| {
                let Some((mut Groups, ResourceCount, GuideRecipes, CrossAirByWire)) =
                    PreparedDomain
                else {
                    return Ok(None);
                };
                let MemberExpansionCount = std::sync::atomic::AtomicUsize::new(0);
                let AssignmentStartedAt = Instant::now();
                let mut Assignment = SolveLayeredCatalogCandidateGroups(
                    &mut Groups,
                    ResourceCount,
                    CrossAirByWire,
                    None,
                    RemainingMemberExpansionCount,
                    &MemberExpansionCount,
                    &Deadline,
                )?;
                if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
                    eprintln!(
                        "native layered member assignment member={} expansions={} elapsed={:.3}s success={}",
                        Member.0,
                        MemberExpansionCount.load(std::sync::atomic::Ordering::SeqCst),
                        AssignmentStartedAt.elapsed().as_secs_f64(),
                        Assignment.Success,
                    );
                }
                Assignment.ExpansionCount =
                    MemberExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
                Ok(Some((Assignment, GuideRecipes)))
            })();
            let AssignmentOutcome = AssignmentOutcome?;
            if let Some((Assignment, _GuideRecipes)) = AssignmentOutcome.as_ref() {
                AssignmentExpansionCount = AssignmentExpansionCount
                    .saturating_add(Assignment.ExpansionCount)
                    .min(EffectiveMaximumExpansionCount);
            }
            let Some((Assignment, GuideRecipes)) = AssignmentOutcome else {
                let Result = LayeredAccessTemplateResult(
                    "Incomplete",
                    false,
                    false,
                    "assignment-deadline",
                    None,
                    Vec::new(),
                    Vec::new(),
                    AssignmentExpansionCount,
                    false,
                    true,
                    None,
                    FirstConflictSignals,
                    FirstConflictResourceIndices,
                    FirstPairwiseIncompatibleSignals,
                    false,
                    AttemptedTemplateIds,
                    AttemptPairwiseIncompatibleSignals,
                    AttemptFailureNets,
                    AttemptExpansionCounts,
                    AttemptPartialCandidateIds,
                    EscapeExpansionCount,
                    StartedAt,
                );
                return Ok((Result, Some(EscapeResult), Vec::new(), EscapeExpansionCount));
            };
            AttemptFailureNets.push((Member.0.clone(), Assignment.FailureNet.clone()));
            AttemptExpansionCounts.push((Member.0.clone(), Assignment.ExpansionCount));
            AttemptPartialCandidateIds
                .push((Member.0.clone(), Assignment.SelectedCandidateIds.clone()));
            AttemptPairwiseIncompatibleSignals.push((
                Member.0.clone(),
                Assignment.PairwiseIncompatibleSignals.clone(),
            ));
            if FirstConflictSignals.is_empty() {
                FirstConflictSignals = Assignment.ConflictSignals.clone();
                FirstConflictResourceIndices = Assignment.ConflictResourceIndices.clone();
            }
            if FirstPairwiseIncompatibleSignals.is_empty() {
                FirstPairwiseIncompatibleSignals = Assignment.PairwiseIncompatibleSignals.clone();
            }
            if Assignment.DeadlineExceeded || Assignment.BudgetExhausted {
                let Result = LayeredAccessTemplateResult(
                    "Incomplete",
                    false,
                    false,
                    if Assignment.DeadlineExceeded {
                        "assignment-deadline"
                    } else {
                        "assignment-work-cap"
                    },
                    None,
                    Vec::new(),
                    Vec::new(),
                    AssignmentExpansionCount,
                    Assignment.BudgetExhausted,
                    Assignment.DeadlineExceeded,
                    Assignment.FailureNet,
                    FirstConflictSignals,
                    FirstConflictResourceIndices,
                    FirstPairwiseIncompatibleSignals,
                    Assignment.PairwiseCompatibilityComplete,
                    AttemptedTemplateIds,
                    AttemptPairwiseIncompatibleSignals,
                    AttemptFailureNets,
                    AttemptExpansionCounts,
                    AttemptPartialCandidateIds,
                    EscapeExpansionCount,
                    StartedAt,
                );
                return Ok((Result, Some(EscapeResult), Vec::new(), EscapeExpansionCount));
            }
            if Assignment.Success {
                let SelectedAccessCandidateIdByVariable = Assignment
                    .SelectedCandidateIds
                    .iter()
                    .filter(|(Variable, _CandidateId)| Variable.starts_with("__access_terminal__:"))
                    .cloned()
                    .collect::<HashMap<_, _>>();
                let SelectedGuides = Assignment
                    .SelectedCandidateIds
                    .iter()
                    .filter_map(|(_Variable, CandidateId)| GuideRecipes.get(CandidateId).cloned())
                    .map(|mut Guide| {
                        let CertifiedAccessCandidateIds = Guide.2.clone();
                        for (AccessVariable, AccessCandidateId) in &mut Guide.2 {
                            *AccessCandidateId = SelectedAccessCandidateIdByVariable
                                .get(AccessVariable)
                                .expect(
                                    "selected guide portal contract has an exact access witness",
                                )
                                .clone();
                        }
                        if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some()
                            && CertifiedAccessCandidateIds != Guide.2
                        {
                            eprintln!(
                                "native layered selected guide substituted access witness signal={} certified={:?} selected={:?}",
                                Guide.0,
                                CertifiedAccessCandidateIds,
                                Guide.2,
                            );
                        }
                        Guide
                    })
                    .collect::<Vec<_>>();
                let Result = LayeredAccessTemplateResult(
                    "Feasible",
                    true,
                    true,
                    "",
                    Some(Member.0.clone()),
                    Member.1.clone(),
                    Assignment.SelectedCandidateIds,
                    AssignmentExpansionCount,
                    false,
                    false,
                    None,
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    true,
                    AttemptedTemplateIds,
                    AttemptPairwiseIncompatibleSignals,
                    AttemptFailureNets,
                    AttemptExpansionCounts,
                    AttemptPartialCandidateIds,
                    EscapeExpansionCount,
                    StartedAt,
                );
                return Ok((
                    Result,
                    Some(EscapeResult),
                    SelectedGuides,
                    EscapeExpansionCount,
                ));
            }
        }
        MemberStart = MemberEnd;
    }
    let Result = LayeredAccessTemplateResult(
        "Incomplete",
        false,
        false,
        "non-exhaustive-template-domain",
        None,
        Vec::new(),
        Vec::new(),
        AssignmentExpansionCount,
        false,
        false,
        None,
        FirstConflictSignals,
        FirstConflictResourceIndices,
        FirstPairwiseIncompatibleSignals,
        true,
        AttemptedTemplateIds,
        AttemptPairwiseIncompatibleSignals,
        AttemptFailureNets,
        AttemptExpansionCounts,
        AttemptPartialCandidateIds,
        EscapeExpansionCount,
        StartedAt,
    );
    Ok((Result, None, Vec::new(), EscapeExpansionCount))
}

/// Evaluate independently fingerprinted layer/member graphs in one native
/// operation.  Every member retains its exact adjacency, request masks, and
/// finite expansion bound; only scheduling and the absolute deadline are
/// shared.  The caller sets the portfolio cap to the sum of those immutable
/// member bounds, so parallel execution cannot borrow work from another
/// physical world or make completion order-dependent.
pub(crate) fn BuildLayeredEscapeStatePathCatalogWithDeadline(
    Members: Vec<LayeredEscapeMemberRequest>,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
) -> (String, Vec<LayeredEscapeMemberResult>, usize, bool, bool) {
    let DeclaredExpansionUpperBound = Members
        .iter()
        .fold(0usize, |Total, Member| Total.saturating_add(Member.3));
    if DeclaredExpansionUpperBound > MaximumExpansionCount {
        return ("WorkCapExceeded".to_string(), Vec::new(), 0, true, false);
    }
    let mut Outcomes: Vec<LayeredEscapeMemberResult> = Vec::with_capacity(Members.len());
    for (MemberId, Adjacency, Requests, MemberExpansionLimit) in Members {
        let (Status, Results, ExpansionCount, WorkCapExceeded, DeadlineExceeded) =
            BuildDerivedEscapeStatePathsWithDeadline(
                Adjacency,
                Requests,
                BendPenalty,
                MemberExpansionLimit,
                Deadline.clone(),
            );
        Outcomes.push((
            MemberId,
            Status,
            Results,
            ExpansionCount,
            WorkCapExceeded,
            DeadlineExceeded,
        ));
        if DeadlineExceeded {
            break;
        }
    }
    let ExpansionCount = Outcomes
        .iter()
        .fold(0usize, |Total, Result| Total.saturating_add(Result.3));
    let WorkCapExceeded =
        Outcomes.iter().any(|Result| Result.4) || ExpansionCount > MaximumExpansionCount;
    let DeadlineExceeded = Outcomes.iter().any(|Result| Result.5);
    let Status = if DeadlineExceeded {
        "DeadlineExceeded"
    } else if WorkCapExceeded {
        "WorkCapExceeded"
    } else {
        "Complete"
    };
    (
        Status.to_string(),
        Outcomes,
        ExpansionCount,
        WorkCapExceeded,
        DeadlineExceeded,
    )
}

fn ReuseExactEscapeRequestResultAtMaximumY(
    CachedRequest: &EscapeRequest,
    CachedResult: &EscapeRequestResult,
    Request: &EscapeRequest,
    MaximumY: i32,
) -> Option<EscapeRequestResult> {
    if !CachedResult.3
        || CachedRequest.0 != Request.0
        || CachedRequest.1 != Request.1
        || CachedRequest.3 != Request.3
        || CachedRequest.4 != Request.4
        || CachedRequest.5 != Request.5
        || CachedRequest.6 != Request.6
    {
        return None;
    }
    let CachedIngresses = CachedRequest.2.iter().copied().collect::<BTreeSet<_>>();
    let RequestedIngresses = Request.2.iter().copied().collect::<BTreeSet<_>>();
    if !RequestedIngresses.is_subset(&CachedIngresses) {
        return None;
    }
    let RelevantCandidates = CachedResult
        .1
        .iter()
        .filter(|(Ingress, _Direction, _Path)| RequestedIngresses.contains(Ingress))
        .cloned()
        .collect::<Vec<_>>();
    if RelevantCandidates
        .iter()
        .any(|(_Ingress, _Direction, Path)| {
            Path.iter().any(|PositionValue| PositionValue.1 > MaximumY)
        })
    {
        return None;
    }
    Some((Request.0.clone(), RelevantCandidates, 0, true))
}

/// Traverse every exact layer view over a shared set of immutable source
/// graphs.  A view owns its own request mask, Y ceiling, and finite work cap;
/// sharing a graph index never projects a path from another layer.  Rayon may
/// schedule independent views concurrently, while indexed collection keeps
/// the declared member order deterministic.
pub(crate) fn BuildLayeredAccessEscapeViewCatalogWithDeadline(
    Graphs: Vec<LayeredAccessEscapeGraphValue>,
    Members: Vec<LayeredAccessEscapeMemberValue>,
    BendPenalty: usize,
    MaximumExpansionCount: usize,
    Deadline: RuntimeDeadline,
) -> PyResult<(String, Vec<LayeredEscapeMemberResult>, usize, bool, bool)> {
    if Graphs.is_empty() || Members.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access view catalog requires graphs and members",
        ));
    }
    if Graphs
        .iter()
        .any(|(GraphId, _Adjacency)| GraphId.is_empty())
        || Graphs
            .iter()
            .map(|(GraphId, _Adjacency)| GraphId)
            .collect::<BTreeSet<_>>()
            .len()
            != Graphs.len()
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access view graph ids must be nonempty and unique",
        ));
    }
    if Members.iter().any(|Member| {
        Member.0.is_empty() || Member.2 >= Graphs.len() || Member.3.is_empty() || Member.6 < 1
    }) || Members
        .iter()
        .map(|Member| &Member.0)
        .collect::<BTreeSet<_>>()
        .len()
        != Members.len()
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access views require exact unique identities, graphs, requests, and caps",
        ));
    }
    let DeclaredExpansionUpperBound = Members
        .iter()
        .fold(0usize, |Total, Member| Total.saturating_add(Member.6));
    if DeclaredExpansionUpperBound > MaximumExpansionCount {
        return Ok(("WorkCapExceeded".to_string(), Vec::new(), 0, true, false));
    }
    let ReferencedGraphIndices = Members
        .iter()
        .map(|Member| Member.2)
        .collect::<BTreeSet<_>>();
    let PreparedGraphs = Graphs
        .into_iter()
        .enumerate()
        .map(|(GraphIndex, (_GraphId, Adjacency))| {
            ReferencedGraphIndices
                .contains(&GraphIndex)
                .then(|| Arc::new(PreparedEscapeTraversalGraph::New(Adjacency)))
        })
        .collect::<Vec<_>>();
    let mut MembersByGraph = BTreeMap::<usize, Vec<(usize, LayeredAccessEscapeMemberValue)>>::new();
    for (MemberIndex, Member) in Members.into_iter().enumerate() {
        MembersByGraph
            .entry(Member.2)
            .or_default()
            .push((MemberIndex, Member));
    }
    for GraphMembers in MembersByGraph.values_mut() {
        GraphMembers.sort_by(|First, Second| {
            Second
                .1
                 .5
                .cmp(&First.1 .5)
                .then_with(|| First.1 .1.cmp(&Second.1 .1))
                .then_with(|| First.1 .0.cmp(&Second.1 .0))
        });
    }
    let MemberCount = MembersByGraph.values().map(Vec::len).sum::<usize>();
    let mut IndexedOutcomes = std::iter::repeat_with(|| None)
        .take(MemberCount)
        .collect::<Vec<Option<LayeredEscapeMemberResult>>>();
    let mut CachedByGraphAndRequestId = MembersByGraph
        .keys()
        .map(|GraphIndex| {
            (
                *GraphIndex,
                HashMap::<String, (EscapeRequest, EscapeRequestResult)>::new(),
            )
        })
        .collect::<HashMap<_, _>>();
    let MaximumWaveCount = MembersByGraph.values().map(Vec::len).max().unwrap_or(0);
    for WaveIndex in 0..MaximumWaveCount {
        struct PreparedWaveMember {
            OriginalIndex: usize,
            MemberId: String,
            GraphIndex: usize,
            Requests: Vec<EscapeRequest>,
            ReusedResults: HashMap<String, EscapeRequestResult>,
            ExpansionLimit: usize,
            ExpectedUnitCounts: Vec<usize>,
        }
        struct WaveWorkUnit {
            UnitIndex: usize,
            MemberIndex: usize,
            GraphIndex: usize,
            RequestIndex: usize,
            MaximumY: i32,
            Request: EscapeRequest,
        }

        let mut PreparedMembers = Vec::<PreparedWaveMember>::new();
        let mut WorkUnits = Vec::<WaveWorkUnit>::new();
        for (GraphIndex, GraphMembers) in &MembersByGraph {
            let Some((
                OriginalIndex,
                (
                    MemberId,
                    _Objective,
                    _MemberGraphIndex,
                    MemberRequests,
                    _RequestMetadata,
                    MaximumY,
                    MemberExpansionLimit,
                ),
            )) = GraphMembers.get(WaveIndex)
            else {
                continue;
            };
            let mut Requests = MemberRequests.clone();
            for Request in &mut Requests {
                Request.2.retain(|Ingress| Ingress.1 <= *MaximumY);
            }
            let CachedByRequestId = CachedByGraphAndRequestId
                .get(GraphIndex)
                .expect("referenced graph cache");
            let mut ReusedResults = HashMap::<String, EscapeRequestResult>::new();
            let MemberIndex = PreparedMembers.len();
            let mut ExpectedUnitCounts = vec![0usize; Requests.len()];
            for (RequestIndex, Request) in Requests.iter().enumerate() {
                let Reused =
                    CachedByRequestId
                        .get(&Request.0)
                        .and_then(|(CachedRequest, CachedResult)| {
                            ReuseExactEscapeRequestResultAtMaximumY(
                                CachedRequest,
                                CachedResult,
                                Request,
                                *MaximumY,
                            )
                        });
                if let Some(ResultValue) = Reused {
                    ReusedResults.insert(Request.0.clone(), ResultValue);
                } else if Request.6 && Request.2.len() > 1 {
                    for Ingress in &Request.2 {
                        let mut UnitRequest = Request.clone();
                        UnitRequest.2 = vec![*Ingress];
                        WorkUnits.push(WaveWorkUnit {
                            UnitIndex: WorkUnits.len(),
                            MemberIndex,
                            GraphIndex: *GraphIndex,
                            RequestIndex,
                            MaximumY: *MaximumY,
                            Request: UnitRequest,
                        });
                        ExpectedUnitCounts[RequestIndex] += 1;
                    }
                } else {
                    WorkUnits.push(WaveWorkUnit {
                        UnitIndex: WorkUnits.len(),
                        MemberIndex,
                        GraphIndex: *GraphIndex,
                        RequestIndex,
                        MaximumY: *MaximumY,
                        Request: Request.clone(),
                    });
                    ExpectedUnitCounts[RequestIndex] = 1;
                }
            }
            PreparedMembers.push(PreparedWaveMember {
                OriginalIndex: *OriginalIndex,
                MemberId: MemberId.clone(),
                GraphIndex: *GraphIndex,
                Requests,
                ReusedResults,
                ExpansionLimit: *MemberExpansionLimit,
                ExpectedUnitCounts,
            });
        }

        let ExpansionBudgets = PreparedMembers
            .iter()
            .map(|Member| Arc::new(SharedEscapeExpansionBudget::New(Member.ExpansionLimit)))
            .collect::<Vec<_>>();
        let WorkerCount = RoutingThreadPool()
            .current_num_threads()
            .max(1)
            .min(WorkUnits.len().max(1));
        let mut WorkShards = (0..WorkerCount)
            .map(|_| Vec::<WaveWorkUnit>::new())
            .collect::<Vec<_>>();
        for WorkUnit in WorkUnits {
            let MixedUnitIndex =
                WorkUnit.UnitIndex ^ (WorkUnit.UnitIndex >> 3) ^ (WorkUnit.UnitIndex >> 6);
            WorkShards[MixedUnitIndex % WorkerCount].push(WorkUnit);
        }
        for WorkShard in &mut WorkShards {
            WorkShard.sort_by(|First, Second| {
                First
                    .GraphIndex
                    .cmp(&Second.GraphIndex)
                    .then_with(|| First.MemberIndex.cmp(&Second.MemberIndex))
                    .then_with(|| First.UnitIndex.cmp(&Second.UnitIndex))
            });
        }
        let ShardOutcomes = RoutingThreadPool().install(|| {
            WorkShards
                .into_par_iter()
                .map(|WorkShard| {
                    let mut ActiveGraphIndex = usize::MAX;
                    let mut Workspace = None::<IndexedEscapeWorkspace>;
                    let mut Outcomes = Vec::with_capacity(WorkShard.len());
                    for WorkUnit in WorkShard {
                        if Deadline.Check() {
                            break;
                        }
                        if ActiveGraphIndex != WorkUnit.GraphIndex {
                            let PreparedGraph = PreparedGraphs[WorkUnit.GraphIndex]
                                .as_ref()
                                .expect("referenced graph is prepared");
                            Workspace =
                                Some(IndexedEscapeWorkspace::New(PreparedGraph.IndexedStateCount));
                            ActiveGraphIndex = WorkUnit.GraphIndex;
                        }
                        let PreparedGraph = PreparedGraphs[WorkUnit.GraphIndex]
                            .as_ref()
                            .expect("referenced graph is prepared");
                        let mut ExpansionLease = SharedEscapeExpansionLease::New(
                            ExpansionBudgets[WorkUnit.MemberIndex].as_ref(),
                        );
                        let (Result, WorkCapExceeded, DeadlineExceeded) =
                            BuildOneDerivedEscapeRequest(
                                &PreparedGraph.Adjacency,
                                &PreparedGraph.IndexedGraph,
                                Workspace.as_mut().expect("active graph workspace"),
                                WorkUnit.Request,
                                BendPenalty,
                                PreparedMembers[WorkUnit.MemberIndex].ExpansionLimit,
                                Deadline.clone(),
                                Some(WorkUnit.MaximumY),
                                Some(&mut ExpansionLease),
                            );
                        Outcomes.push((
                            WorkUnit.UnitIndex,
                            WorkUnit.MemberIndex,
                            WorkUnit.RequestIndex,
                            Result,
                            WorkCapExceeded,
                            DeadlineExceeded,
                        ));
                        if DeadlineExceeded {
                            break;
                        }
                    }
                    Outcomes
                })
                .collect::<Vec<_>>()
        });
        let DeadlineExceeded = Deadline.Check()
            || ShardOutcomes.iter().flatten().any(
                |(_UnitIndex, _MemberIndex, _RequestIndex, _Result, _WorkCap, DeadlineValue)| {
                    *DeadlineValue
                },
            );
        let mut UnitOutcomes = ShardOutcomes.into_iter().flatten().collect::<Vec<_>>();
        UnitOutcomes.sort_by_key(|Value| Value.0);
        for (MemberIndex, Member) in PreparedMembers.into_iter().enumerate() {
            let mut CompletedUnitCounts = vec![0usize; Member.Requests.len()];
            let mut ResultByRequestId = Member.ReusedResults;
            let mut MemberWorkCapExceeded = false;
            let mut PendingResults = Member
                .Requests
                .iter()
                .map(|Request| (Request.0.clone(), Vec::new(), 0usize, true))
                .collect::<Vec<EscapeRequestResult>>();
            for (
                _UnitIndex,
                UnitMemberIndex,
                RequestIndex,
                UnitResult,
                UnitWorkCapExceeded,
                _UnitDeadlineExceeded,
            ) in UnitOutcomes.iter().filter(|Value| Value.1 == MemberIndex)
            {
                CompletedUnitCounts[*RequestIndex] += 1;
                PendingResults[*RequestIndex]
                    .1
                    .extend(UnitResult.1.iter().cloned());
                PendingResults[*RequestIndex].2 =
                    PendingResults[*RequestIndex].2.saturating_add(UnitResult.2);
                PendingResults[*RequestIndex].3 &= UnitResult.3;
                MemberWorkCapExceeded |= *UnitWorkCapExceeded;
                debug_assert_eq!(*UnitMemberIndex, MemberIndex);
            }
            for RequestIndex in 0..PendingResults.len() {
                if Member.ExpectedUnitCounts[RequestIndex] == 0 {
                    continue;
                }
                PendingResults[RequestIndex].3 &=
                    CompletedUnitCounts[RequestIndex] == Member.ExpectedUnitCounts[RequestIndex];
                if PendingResults[RequestIndex].3 {
                    let ResultValue = PendingResults[RequestIndex].clone();
                    ResultByRequestId.insert(ResultValue.0.clone(), ResultValue);
                }
            }
            let OrderedResults = Member
                .Requests
                .iter()
                .filter_map(|Request| ResultByRequestId.get(&Request.0).cloned())
                .collect::<Vec<_>>();
            let ExpansionCount = ExpansionBudgets[MemberIndex].ExpansionCount();
            MemberWorkCapExceeded |= !DeadlineExceeded
                && Member
                    .ExpectedUnitCounts
                    .iter()
                    .zip(&CompletedUnitCounts)
                    .any(|(Expected, Completed)| Expected != Completed);
            let MemberComplete = !MemberWorkCapExceeded
                && !DeadlineExceeded
                && OrderedResults.len() == Member.Requests.len()
                && OrderedResults.iter().all(|ResultValue| ResultValue.3);
            let EffectiveStatus = if MemberComplete {
                "Complete"
            } else if DeadlineExceeded {
                "DeadlineExceeded"
            } else {
                "WorkCapExceeded"
            };
            if MemberComplete {
                let CachedByRequestId = CachedByGraphAndRequestId
                    .get_mut(&Member.GraphIndex)
                    .expect("referenced graph cache");
                for (Request, ResultValue) in Member
                    .Requests
                    .iter()
                    .cloned()
                    .zip(OrderedResults.iter().cloned())
                {
                    CachedByRequestId.insert(Request.0.clone(), (Request, ResultValue));
                }
            }
            IndexedOutcomes[Member.OriginalIndex] = Some((
                Member.MemberId,
                EffectiveStatus.to_string(),
                OrderedResults,
                ExpansionCount,
                MemberWorkCapExceeded,
                DeadlineExceeded,
            ));
        }
        if DeadlineExceeded {
            break;
        }
    }
    let Outcomes = IndexedOutcomes.into_iter().flatten().collect::<Vec<_>>();
    let ExpansionCount = Outcomes
        .iter()
        .fold(0usize, |Total, Result| Total.saturating_add(Result.3));
    let WorkCapExceeded =
        Outcomes.iter().any(|Result| Result.4) || ExpansionCount > MaximumExpansionCount;
    let DeadlineExceeded = Outcomes.iter().any(|Result| Result.5);
    let Status = if DeadlineExceeded {
        "DeadlineExceeded"
    } else if WorkCapExceeded {
        "WorkCapExceeded"
    } else {
        "Complete"
    };
    Ok((
        Status.to_string(),
        Outcomes,
        ExpansionCount,
        WorkCapExceeded,
        DeadlineExceeded,
    ))
}

#[cfg(test)]
mod Tests {
    use super::*;

    #[test]
    fn AccessClaimCompositionRejectsEmergentCrossPathAirSupportConflict() {
        let First = BuildDeferredAccessCandidate(
            "first".to_string(),
            "first-value".to_string(),
            "signal".to_string(),
            2,
            vec![(75, 2, 14)],
        )
        .expect("single-cell access claim");
        let Second = BuildDeferredAccessCandidate(
            "second".to_string(),
            "second-value".to_string(),
            "signal".to_string(),
            3,
            vec![(75, 3, 13), (75, 4, 14)],
        )
        .expect("individually legal rising access claim");

        assert!(DeferredAccessCandidatesConflict(&First, &Second));
    }

    #[test]
    fn LayeredGuideContractBindsPortalBeforeExactStubSelection() {
        let Variable = "__access_terminal__:Net:root".to_string();
        let CandidateId = "stub-choice-7".to_string();
        let Contract = BuildLayeredGuideAccessContract(
            &[(Variable.clone(), CandidateId.clone())],
            &HashMap::from([((Variable, CandidateId), (3, 2, -4))]),
        );

        assert_eq!(Contract, "access-portal:Net:root=3,2,-4");
        let Requirements = ParseContractRequirements(&Contract);
        assert!(!Requirements.contains(&(
            "access-stub:Net:root".to_string(),
            "stub-choice-7".to_string(),
        )));
        assert!(
            Requirements.contains(&("access-portal:Net:root".to_string(), "3,2,-4".to_string(),))
        );
    }

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

    #[test]
    fn BoundedRequestWavesStayCompleteBelowTheSharedCap() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let C = (10, 0, 0);
        let D = (11, 0, 0);
        let Result = BuildDerivedEscapeStatePathsWithDeadline(
            vec![(A, vec![B]), (B, vec![A]), (C, vec![D]), (D, vec![C])],
            vec![
                (
                    "first".to_string(),
                    A,
                    vec![B],
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    true,
                ),
                (
                    "second".to_string(),
                    C,
                    vec![D],
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    true,
                ),
            ],
            4,
            200,
            RuntimeDeadline::Unlimited(),
        );

        assert_eq!(Result.0, "Complete");
        assert_eq!(Result.1.len(), 2);
        assert!(Result.1.iter().all(|Request| Request.3));
        assert!(Result.2 <= 200);
        assert!(!Result.3);
        assert!(!Result.4);
    }

    #[test]
    fn EmptyAllowedNodeMaskIsTheExactFullGraphSentinel() {
        let Adjacency = vec![
            ((0, 0, 0), vec![(1, 0, 0), (0, 0, 1)]),
            ((1, 0, 0), vec![(0, 0, 0), (1, 0, 1)]),
            ((0, 0, 1), vec![(0, 0, 0), (1, 0, 1)]),
            ((1, 0, 1), vec![(1, 0, 0), (0, 0, 1)]),
        ];
        let BuildRequest = |AllowedNodes: Vec<Position>| {
            vec![(
                "request".to_string(),
                (0, 0, 0),
                vec![(1, 0, 1)],
                Vec::new(),
                Vec::new(),
                AllowedNodes,
                false,
            )]
        };
        let Explicit = BuildDerivedEscapeStatePathsWithDeadline(
            Adjacency.clone(),
            BuildRequest(vec![(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1)]),
            4,
            128,
            RuntimeDeadline::Unlimited(),
        );
        let Sentinel = BuildDerivedEscapeStatePathsWithDeadline(
            Adjacency,
            BuildRequest(Vec::new()),
            4,
            128,
            RuntimeDeadline::Unlimited(),
        );

        assert_eq!(Sentinel, Explicit);
    }

    #[test]
    fn LayeredCatalogPreservesMemberOrderAndExactGraphs() {
        let BuildMember = |MemberId: &str, Offset: i32| {
            (
                MemberId.to_string(),
                vec![
                    ((Offset, 0, 0), vec![(Offset + 1, 0, 0)]),
                    ((Offset + 1, 0, 0), vec![(Offset, 0, 0)]),
                ],
                vec![(
                    "request".to_string(),
                    (Offset, 0, 0),
                    vec![(Offset + 1, 0, 0)],
                    Vec::new(),
                    Vec::new(),
                    vec![(Offset, 0, 0), (Offset + 1, 0, 0)],
                    false,
                )],
                3usize,
            )
        };
        let Result = BuildLayeredEscapeStatePathCatalogWithDeadline(
            vec![BuildMember("upper", 0), BuildMember("lower", 10)],
            4,
            6,
            RuntimeDeadline::Unlimited(),
        );

        assert_eq!(Result.0, "Complete");
        assert_eq!(Result.1[0].0, "upper");
        assert_eq!(Result.1[1].0, "lower");
        assert_eq!(Result.1[0].2[0].1[0].2.first(), Some(&(0, 0, 0)));
        assert_eq!(Result.1[1].2[0].1[0].2.first(), Some(&(10, 0, 0)));
        assert!(!Result.3);
        assert!(!Result.4);
    }

    #[test]
    fn LayeredCatalogRejectsAnInsufficientSharedCapBeforeWork() {
        let Result = BuildLayeredEscapeStatePathCatalogWithDeadline(
            vec![("member".to_string(), Vec::new(), Vec::new(), 2usize)],
            4,
            1,
            RuntimeDeadline::Unlimited(),
        );

        assert_eq!(Result.0, "WorkCapExceeded");
        assert!(Result.1.is_empty());
        assert_eq!(Result.2, 0);
        assert!(Result.3);
        assert!(!Result.4);
    }

    #[test]
    fn LayeredAccessViewCatalogSearchesEachCeilingExactly() {
        let Graph = vec![
            ((0, 0, 0), vec![(0, 0, 1), (1, 1, 0)]),
            ((0, 0, 1), vec![(0, 0, 0), (1, 0, 1)]),
            ((1, 0, 1), vec![(0, 0, 1), (2, 0, 1)]),
            ((2, 0, 1), vec![(1, 0, 1), (2, 0, 0)]),
            ((1, 1, 0), vec![(0, 0, 0), (2, 0, 0)]),
            ((2, 0, 0), vec![(2, 0, 1), (1, 1, 0)]),
        ];
        let BuildMember = |MemberId: &str, MaximumY: i32| {
            (
                MemberId.to_string(),
                vec![0],
                0usize,
                vec![(
                    "request".to_string(),
                    (0, 0, 0),
                    vec![(2, 0, 0)],
                    Vec::new(),
                    Vec::new(),
                    Vec::new(),
                    false,
                )],
                vec![(
                    "request".to_string(),
                    "__access_terminal__:signal:root".to_string(),
                    "signal".to_string(),
                )],
                MaximumY,
                128usize,
            )
        };
        let Result = BuildLayeredAccessEscapeViewCatalogWithDeadline(
            vec![("graph".to_string(), Graph)],
            vec![BuildMember("lower", 0), BuildMember("upper", 1)],
            4,
            256,
            RuntimeDeadline::Unlimited(),
        )
        .expect("layered access view catalog should be valid");

        assert_eq!(Result.0, "Complete");
        assert_eq!(Result.1[0].0, "lower");
        assert_eq!(Result.1[1].0, "upper");
        let LowerPaths = &Result.1[0].2[0].1;
        let UpperPaths = &Result.1[1].2[0].1;
        assert!(LowerPaths
            .iter()
            .all(|Value| { Value.2.iter().all(|PositionValue| PositionValue.1 <= 0) }));
        assert!(UpperPaths
            .iter()
            .any(|Value| { Value.2.contains(&(1, 1, 0)) }));
        assert!(!Result.3);
        assert!(!Result.4);
    }

    #[test]
    fn LayeredAccessSelectionKeepsOneIngressLayerPerOwnerSignal() {
        let Requests = vec![
            (
                "a-low".to_string(),
                (0, 0, 0),
                vec![(1, 0, 0)],
                Vec::new(),
                vec![(0, 0, 0)],
                Vec::new(),
                true,
            ),
            (
                "a-high".to_string(),
                (0, 2, 0),
                vec![(1, 2, 0)],
                Vec::new(),
                vec![(-2, 2, 0), (-1, 2, 0), (0, 2, 0)],
                Vec::new(),
                true,
            ),
            (
                "b-low".to_string(),
                (10, 0, 0),
                vec![(11, 0, 0)],
                Vec::new(),
                vec![(8, 0, 0), (9, 0, 0), (10, 0, 0)],
                Vec::new(),
                true,
            ),
            (
                "b-high".to_string(),
                (10, 2, 0),
                vec![(11, 2, 0)],
                Vec::new(),
                vec![(10, 2, 0)],
                Vec::new(),
                true,
            ),
        ];
        let RequestResults = vec![
            (
                "a-low".to_string(),
                vec![((1, 0, 0), (1, 0, 0), vec![(0, 0, 0), (1, 0, 0)])],
                1,
                true,
            ),
            (
                "a-high".to_string(),
                vec![((1, 2, 0), (1, 0, 0), vec![(0, 2, 0), (1, 2, 0)])],
                1,
                true,
            ),
            (
                "b-low".to_string(),
                vec![((11, 0, 0), (1, 0, 0), vec![(10, 0, 0), (11, 0, 0)])],
                1,
                true,
            ),
            (
                "b-high".to_string(),
                vec![((11, 2, 0), (1, 0, 0), vec![(10, 2, 0), (11, 2, 0)])],
                1,
                true,
            ),
        ];
        let RequestMetadata = vec![
            (
                "a-low".to_string(),
                "__access_terminal__:Net:root".to_string(),
                "Net".to_string(),
            ),
            (
                "a-high".to_string(),
                "__access_terminal__:Net:root".to_string(),
                "Net".to_string(),
            ),
            (
                "b-low".to_string(),
                "__access_terminal__:Net:target-0".to_string(),
                "Net".to_string(),
            ),
            (
                "b-high".to_string(),
                "__access_terminal__:Net:target-0".to_string(),
                "Net".to_string(),
            ),
        ];
        let (mut Groups, ResourceCount) = BuildLayeredAccessCandidateGroups(
            &Requests,
            &RequestResults,
            &RequestMetadata,
            &RuntimeDeadline::Unlimited(),
        )
        .expect("valid layered access fixture")
        .expect("unlimited fixture cannot expire");
        let Result = PlanAuthoritativeCandidateGroupsWithInitialExpansionAndDeadline(
            &mut Groups,
            ResourceCount,
            0,
            64,
            RuntimeDeadline::Unlimited(),
            true,
            true,
            None,
        )
        .expect("layer-coherent access assignment");

        assert!(Result.Success);
        assert_eq!(
            Result.SelectedCandidateIds,
            vec![
                (
                    "__access_terminal__:Net:root".to_string(),
                    "a-low#0".to_string(),
                ),
                (
                    "__access_terminal__:Net:target-0".to_string(),
                    "b-low#0".to_string(),
                ),
            ],
        );
    }
}
