//! Exact, repair, and maximum-partial assignment search.

use crate::Core::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Core::Models::AssignmentCandidate;
use crate::Core::Runtime::RoutingThreadPool;
use rayon::prelude::*;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::atomic::{AtomicUsize, Ordering as AtomicOrdering};
use std::sync::Arc;

use super::Domains::*;
use super::Witness::*;

#[allow(clippy::too_many_arguments)]
pub(super) fn AssignIndexedCandidateDomains(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    SignalNames: &[String],
    Compatibility: &CandidateCompatibility,
    Domains: &[CandidateDomain],
    Assigned: &mut [bool],
    Selection: &mut [Option<usize>],
    BestSelection: &mut Vec<Option<usize>>,
    PreferredSelection: Option<&[Option<usize>]>,
    ExpansionCount: &mut usize,
    MaximumExpansionCount: usize,
    BudgetExhausted: &mut bool,
    Deadline: &RuntimeDeadline,
    FailureDepth: &mut usize,
    FailureNet: &mut Option<String>,
    ConflictSignals: &mut Vec<String>,
    ConflictResources: &mut Vec<usize>,
    SharedExpansionCount: Option<&AtomicUsize>,
    EnforceFullArcConsistency: bool,
    PreferGuideFactorVariables: bool,
    ConstraintNeighbors: &[Vec<usize>],
    LocalMaximumExpansionCount: Option<usize>,
) -> bool {
    if *BudgetExhausted || Deadline.Check() {
        return false;
    }
    let SelectionDepth = Selection.iter().filter(|Value| Value.is_some()).count();
    if SelectionDepth > BestSelection.iter().filter(|Value| Value.is_some()).count() {
        *BestSelection = Selection.to_vec();
    }
    if SelectionDepth == SignalNames.len() {
        return SelectionHasPoweredAccessWitness(
            Groups,
            SignalNames,
            Selection,
            Deadline,
            FailureNet,
        ) == Some(true);
    }

    let PreferredVariableKind = if PreferGuideFactorVariables
        && (0..SignalNames.len()).any(|SignalIndex| {
            !Assigned[SignalIndex] && SignalNames[SignalIndex].starts_with("__base_claim__:")
        }) {
        Some(0usize)
    } else if PreferGuideFactorVariables
        && (0..SignalNames.len()).any(|SignalIndex| {
            !Assigned[SignalIndex]
                && SignalNames[SignalIndex].starts_with("__access_terminal__:")
                && DomainCount(&Domains[SignalIndex]) < Groups[&SignalNames[SignalIndex]].len()
        })
    {
        // A selected guide names an exact portal value for each terminal.
        // Bind those narrowed physical stubs before selecting another guide
        // so guide/access/guide conflicts are propagated as one exact bundle.
        Some(2usize)
    } else if PreferGuideFactorVariables
        && (0..SignalNames.len()).any(|SignalIndex| {
            !Assigned[SignalIndex]
                && IsDetachedInternalGuideVariable(Groups, &SignalNames[SignalIndex])
        })
    {
        Some(1usize)
    } else if PreferGuideFactorVariables
        && (0..SignalNames.len()).any(|SignalIndex| {
            !Assigned[SignalIndex] && SignalNames[SignalIndex].starts_with("__route_guide__:")
        })
    {
        Some(3usize)
    } else {
        None
    };
    let mut SelectedSignal = None;
    for SignalIndex in 0..SignalNames.len() {
        if Assigned[SignalIndex] {
            continue;
        }
        let VariableKind = if SignalNames[SignalIndex].starts_with("__base_claim__:") {
            0usize
        } else if IsDetachedInternalGuideVariable(Groups, &SignalNames[SignalIndex]) {
            1usize
        } else if SignalNames[SignalIndex].starts_with("__route_guide__:") {
            3usize
        } else {
            2usize
        };
        if PreferredVariableKind.is_some_and(|Preferred| VariableKind != Preferred) {
            continue;
        }
        let Count = if PreferGuideFactorVariables
            && SignalNames[SignalIndex].starts_with("__route_guide__:")
        {
            (0..Groups[&SignalNames[SignalIndex]].len())
                .filter(|CandidateIndex| {
                    DomainContains(&Domains[SignalIndex], *CandidateIndex)
                        && GuideCandidateHasAvailableCertifiedTuple(
                            Groups,
                            SignalNames,
                            Domains,
                            SignalIndex,
                            *CandidateIndex,
                        )
                })
                .count()
        } else {
            DomainCount(&Domains[SignalIndex])
        };
        if Count == 0 {
            *FailureNet = Some(SignalNames[SignalIndex].clone());
            return false;
        }
        if SelectedSignal.is_none_or(|(BestIndex, BestCount)| {
            (Count, &SignalNames[SignalIndex]) < (BestCount, &SignalNames[BestIndex])
        }) {
            SelectedSignal = Some((SignalIndex, Count));
        }
    }
    let SignalIndex = SelectedSignal.unwrap().0;
    let CandidateCount = Groups[&SignalNames[SignalIndex]].len();
    let mut CandidateIndices = (0..CandidateCount)
        .filter(|CandidateIndex| {
            DomainContains(&Domains[SignalIndex], *CandidateIndex)
                && (!PreferGuideFactorVariables
                    || !SignalNames[SignalIndex].starts_with("__route_guide__:")
                    || GuideCandidateHasAvailableCertifiedTuple(
                        Groups,
                        SignalNames,
                        Domains,
                        SignalIndex,
                        *CandidateIndex,
                    ))
        })
        .map(|CandidateIndex| {
            let CompatibleCounts = (0..SignalNames.len())
                .filter(|OtherSignalIndex| {
                    !Assigned[*OtherSignalIndex] && *OtherSignalIndex != SignalIndex
                })
                .map(|OtherSignalIndex| {
                    IntersectionCount(
                        &Domains[OtherSignalIndex],
                        &Compatibility[SignalIndex][CandidateIndex][OtherSignalIndex],
                    )
                })
                .collect::<Vec<_>>();
            (
                CandidateIndex,
                CompatibleCounts.iter().copied().min().unwrap_or(0),
                CompatibleCounts.iter().sum::<usize>(),
            )
        })
        .collect::<Vec<_>>();
    CandidateIndices.sort_by(
        |(FirstIndex, FirstMinimum, FirstTotal), (SecondIndex, SecondMinimum, SecondTotal)| {
            if PreferGuideFactorVariables
                && SignalNames[SignalIndex].starts_with("__route_guide__:")
            {
                // Compact guide candidates are already sorted by their exact
                // physical objective and materializability evidence.  Keep
                // that order authoritative and use pairwise freedom only as
                // a tie breaker.  Reversing these keys can sacrifice a short,
                // certified internal corridor merely because a remote guide
                // leaves more cheap output choices; the first complete greedy
                // bundle then has no selected-world materialization witness.
                (
                    *FirstIndex,
                    std::cmp::Reverse(*FirstMinimum),
                    std::cmp::Reverse(*FirstTotal),
                )
                    .cmp(&(
                        *SecondIndex,
                        std::cmp::Reverse(*SecondMinimum),
                        std::cmp::Reverse(*SecondTotal),
                    ))
            } else {
                (
                    std::cmp::Reverse(*FirstMinimum),
                    std::cmp::Reverse(*FirstTotal),
                    *FirstIndex,
                )
                    .cmp(&(
                        std::cmp::Reverse(*SecondMinimum),
                        std::cmp::Reverse(*SecondTotal),
                        *SecondIndex,
                    ))
            }
        },
    );
    if SignalNames[SignalIndex].starts_with("__route_guide__:") {
        let HasAvailableCertifiedTuple = |CandidateIndex: usize| {
            Groups[&SignalNames[SignalIndex]][CandidateIndex]
                .PoweredAccessConstraint
                .as_ref()
                .is_some_and(|Constraint| {
                    Constraint
                        .PreferredAccessCandidateTuples
                        .iter()
                        .any(|CandidateTuple| {
                            CandidateTuple.iter().all(|(Variable, CandidateId)| {
                                let Some(TupleSignalIndex) =
                                    SignalNames.iter().position(|Signal| Signal == Variable)
                                else {
                                    return false;
                                };
                                Groups[Variable]
                                    .iter()
                                    .position(|Candidate| Candidate.CandidateId == *CandidateId)
                                    .is_some_and(|AccessCandidateIndex| {
                                        DomainContains(
                                            &Domains[TupleSignalIndex],
                                            AccessCandidateIndex,
                                        )
                                    })
                            })
                        })
                })
        };
        CandidateIndices.sort_by_key(|(CandidateIndex, _Minimum, _Total)| {
            usize::from(!HasAvailableCertifiedTuple(*CandidateIndex))
        });
    }
    if let Some(PreferredCandidateIndex) = PreferredSelection
        .and_then(|Values| Values[SignalIndex])
        .or(BestSelection[SignalIndex])
    {
        if let Some(PreferredPosition) =
            CandidateIndices
                .iter()
                .position(|(CandidateIndex, _Minimum, _Total)| {
                    *CandidateIndex == PreferredCandidateIndex
                })
        {
            CandidateIndices[..=PreferredPosition].rotate_right(1);
        }
    }
    if SignalNames[SignalIndex].starts_with("__access_terminal__:") {
        let CertifiedCandidateId = Selection
            .iter()
            .enumerate()
            .filter_map(|(SelectedSignalIndex, SelectedCandidateIndex)| {
                let SelectedCandidateIndex = (*SelectedCandidateIndex)?;
                Groups[&SignalNames[SelectedSignalIndex]][SelectedCandidateIndex]
                    .PoweredAccessConstraint
                    .as_ref()
            })
            .find_map(|Constraint| {
                Constraint
                    .PreferredAccessCandidateTuples
                    .iter()
                    .find(|CandidateTuple| {
                        CandidateTuple.iter().all(|(Variable, CandidateId)| {
                            let Some(TupleSignalIndex) =
                                SignalNames.iter().position(|Signal| Signal == Variable)
                            else {
                                return false;
                            };
                            if let Some(SelectedCandidateIndex) = Selection[TupleSignalIndex] {
                                Groups[Variable][SelectedCandidateIndex].CandidateId == *CandidateId
                            } else {
                                Groups[Variable]
                                    .iter()
                                    .position(|Candidate| Candidate.CandidateId == *CandidateId)
                                    .is_some_and(|CandidateIndex| {
                                        DomainContains(&Domains[TupleSignalIndex], CandidateIndex)
                                    })
                            }
                        })
                    })
                    .and_then(|CandidateTuple| {
                        CandidateTuple
                            .iter()
                            .find(|(Variable, _CandidateId)| Variable == &SignalNames[SignalIndex])
                            .map(|(_Variable, CandidateId)| CandidateId)
                    })
            });
        if let Some(CertifiedCandidateId) = CertifiedCandidateId {
            if let Some(CertifiedPosition) =
                CandidateIndices
                    .iter()
                    .position(|(CandidateIndex, _Minimum, _Total)| {
                        Groups[&SignalNames[SignalIndex]][*CandidateIndex].CandidateId
                            == *CertifiedCandidateId
                    })
            {
                CandidateIndices[..=CertifiedPosition].rotate_right(1);
            }
        }
    }
    for (CandidateIndex, _MinimumCompatible, _TotalCompatible) in CandidateIndices {
        if Deadline.Check() {
            *FailureNet = Some(SignalNames[SignalIndex].clone());
            return false;
        }
        if SignalNames[SignalIndex].starts_with("__route_guide__:")
            && Groups[&SignalNames[SignalIndex]][CandidateIndex]
                .PoweredAccessConstraint
                .is_some()
        {
            match GuideCandidateHasPoweredAccessBundle(
                Groups,
                SignalNames,
                Compatibility,
                Domains,
                Selection,
                SignalIndex,
                CandidateIndex,
                Deadline,
                FailureNet,
            ) {
                Some(true) => {}
                Some(false) => continue,
                None => return false,
            }
        }
        if LocalMaximumExpansionCount.is_some_and(|Maximum| *ExpansionCount >= Maximum) {
            *BudgetExhausted = true;
            *FailureNet = Some(SignalNames[SignalIndex].clone());
            return false;
        }
        let SharedBudgetAvailable = SharedExpansionCount.is_none_or(|Shared| {
            Shared
                .fetch_update(AtomicOrdering::SeqCst, AtomicOrdering::SeqCst, |Value| {
                    (Value < MaximumExpansionCount).then_some(Value + 1)
                })
                .is_ok()
        });
        *ExpansionCount += 1;
        if !SharedBudgetAvailable
            || (SharedExpansionCount.is_none() && *ExpansionCount > MaximumExpansionCount)
        {
            *BudgetExhausted = true;
            *FailureNet = Some(SignalNames[SignalIndex].clone());
            return false;
        }

        let mut NextDomains = Domains.to_vec();
        let mut ChangedSignalIndices = Vec::new();
        let mut Consistent = true;
        for OtherSignalIndex in 0..SignalNames.len() {
            if Assigned[OtherSignalIndex] || OtherSignalIndex == SignalIndex {
                continue;
            }
            let PreviousDomain = NextDomains[OtherSignalIndex].clone();
            IntersectDomain(
                &mut NextDomains[OtherSignalIndex],
                &Compatibility[SignalIndex][CandidateIndex][OtherSignalIndex],
            );
            if NextDomains[OtherSignalIndex] != PreviousDomain {
                ChangedSignalIndices.push(OtherSignalIndex);
            }
            if DomainIsEmpty(&NextDomains[OtherSignalIndex]) {
                let mut FailureSelection = Selection.to_vec();
                FailureSelection[SignalIndex] = Some(CandidateIndex);
                if FailureSelection
                    .iter()
                    .filter(|Value| Value.is_some())
                    .count()
                    > BestSelection.iter().filter(|Value| Value.is_some()).count()
                {
                    *BestSelection = FailureSelection;
                }
                RecordIndexedDeadEnd(
                    Groups,
                    SignalNames,
                    Domains,
                    Selection,
                    SignalIndex,
                    CandidateIndex,
                    OtherSignalIndex,
                    FailureDepth,
                    FailureNet,
                    ConflictSignals,
                    ConflictResources,
                    Deadline,
                );
                Consistent = false;
                break;
            }
        }
        if !Consistent {
            continue;
        }
        Assigned[SignalIndex] = true;
        Selection[SignalIndex] = Some(CandidateIndex);
        if SignalNames[SignalIndex].starts_with("__route_guide__:")
            && Groups[&SignalNames[SignalIndex]][CandidateIndex]
                .PoweredAccessConstraint
                .is_some()
        {
            let CertifiedTuples = Groups[&SignalNames[SignalIndex]][CandidateIndex]
                .PoweredAccessConstraint
                .as_ref()
                .map(|Constraint| Arc::clone(&Constraint.PreferredAccessCandidateTuples))
                .unwrap_or_default();
            let mut CertifiedTupleIndices = (0..CertifiedTuples.len()).collect::<Vec<_>>();
            CertifiedTupleIndices.sort_by_key(|TupleIndex| {
                let CandidateTuple = &CertifiedTuples[*TupleIndex];
                let WarmMismatchCount = CandidateTuple
                    .iter()
                    .filter(|(Variable, CandidateId)| {
                        let Some(AccessSignalIndex) =
                            SignalNames.iter().position(|Signal| Signal == Variable)
                        else {
                            return true;
                        };
                        PreferredSelection
                            .and_then(|Values| Values[AccessSignalIndex])
                            .is_some_and(|PreferredCandidateIndex| {
                                Groups[Variable][PreferredCandidateIndex].CandidateId
                                    != *CandidateId
                            })
                    })
                    .count();
                (
                    usize::from(!CertifiedAccessTupleIsAvailable(
                        Groups,
                        SignalNames,
                        &NextDomains,
                        CandidateTuple,
                    )),
                    WarmMismatchCount,
                    *TupleIndex,
                )
            });
            for TupleIndex in CertifiedTupleIndices {
                let CandidateTuple = &CertifiedTuples[TupleIndex];
                if !CertifiedAccessTupleIsAvailable(
                    Groups,
                    SignalNames,
                    &NextDomains,
                    CandidateTuple,
                ) {
                    continue;
                }
                let mut BundleAssigned = Assigned.to_vec();
                let mut BundleSelection = Selection.to_vec();
                let mut BundleDomains = NextDomains.clone();
                let mut BundleChangedSignalIndices = ChangedSignalIndices.clone();
                let mut BundleConsistent = true;
                let mut NewlyAssignedAccessCount = 0usize;
                for (AccessVariable, AccessCandidateId) in CandidateTuple {
                    let Some(AccessSignalIndex) = SignalNames
                        .iter()
                        .position(|Variable| Variable == AccessVariable)
                    else {
                        BundleConsistent = false;
                        break;
                    };
                    let Some(AccessCandidateIndex) = Groups[AccessVariable]
                        .iter()
                        .position(|Candidate| Candidate.CandidateId == *AccessCandidateId)
                    else {
                        BundleConsistent = false;
                        break;
                    };
                    if !DomainContains(&BundleDomains[AccessSignalIndex], AccessCandidateIndex)
                        || BundleSelection[AccessSignalIndex]
                            .is_some_and(|SelectedIndex| SelectedIndex != AccessCandidateIndex)
                    {
                        BundleConsistent = false;
                        break;
                    }
                    if BundleSelection[AccessSignalIndex].is_some() {
                        continue;
                    }
                    for OtherSignalIndex in 0..SignalNames.len() {
                        if OtherSignalIndex == AccessSignalIndex {
                            continue;
                        }
                        if let Some(OtherCandidateIndex) = BundleSelection[OtherSignalIndex] {
                            if !DomainContains(
                                &Compatibility[AccessSignalIndex][AccessCandidateIndex]
                                    [OtherSignalIndex],
                                OtherCandidateIndex,
                            ) {
                                BundleConsistent = false;
                                break;
                            }
                            continue;
                        }
                        let PreviousDomain = BundleDomains[OtherSignalIndex].clone();
                        IntersectDomain(
                            &mut BundleDomains[OtherSignalIndex],
                            &Compatibility[AccessSignalIndex][AccessCandidateIndex]
                                [OtherSignalIndex],
                        );
                        if DomainIsEmpty(&BundleDomains[OtherSignalIndex]) {
                            BundleConsistent = false;
                            break;
                        }
                        if BundleDomains[OtherSignalIndex] != PreviousDomain {
                            BundleChangedSignalIndices.push(OtherSignalIndex);
                        }
                    }
                    if !BundleConsistent {
                        break;
                    }
                    BundleAssigned[AccessSignalIndex] = true;
                    BundleSelection[AccessSignalIndex] = Some(AccessCandidateIndex);
                    NewlyAssignedAccessCount += 1;
                }
                if !BundleConsistent {
                    continue;
                }
                let LocalBudgetAvailable = LocalMaximumExpansionCount.is_none_or(|Maximum| {
                    ExpansionCount.saturating_add(NewlyAssignedAccessCount) <= Maximum
                });
                let SharedBudgetAvailable = SharedExpansionCount.is_none_or(|Shared| {
                    Shared
                        .fetch_update(AtomicOrdering::SeqCst, AtomicOrdering::SeqCst, |Value| {
                            Value
                                .checked_add(NewlyAssignedAccessCount)
                                .filter(|Next| *Next <= MaximumExpansionCount)
                        })
                        .is_ok()
                });
                if !LocalBudgetAvailable || !SharedBudgetAvailable {
                    *BudgetExhausted = true;
                    *FailureNet = Some(SignalNames[SignalIndex].clone());
                    Assigned[SignalIndex] = false;
                    Selection[SignalIndex] = None;
                    return false;
                }
                *ExpansionCount = ExpansionCount.saturating_add(NewlyAssignedAccessCount);
                BundleChangedSignalIndices.sort_unstable();
                BundleChangedSignalIndices.dedup();
                if EnforceFullArcConsistency
                    && !EnforceIncrementalArcConsistency(
                        SignalNames,
                        Compatibility,
                        &mut BundleDomains,
                        &BundleAssigned,
                        ConstraintNeighbors,
                        &BundleChangedSignalIndices,
                        Deadline,
                        FailureNet,
                    )
                {
                    if Deadline.WasExceeded() {
                        Assigned[SignalIndex] = false;
                        Selection[SignalIndex] = None;
                        return false;
                    }
                    continue;
                }
                if AssignIndexedCandidateDomains(
                    Groups,
                    SignalNames,
                    Compatibility,
                    &BundleDomains,
                    &mut BundleAssigned,
                    &mut BundleSelection,
                    BestSelection,
                    PreferredSelection,
                    ExpansionCount,
                    MaximumExpansionCount,
                    BudgetExhausted,
                    Deadline,
                    FailureDepth,
                    FailureNet,
                    ConflictSignals,
                    ConflictResources,
                    SharedExpansionCount,
                    EnforceFullArcConsistency,
                    PreferGuideFactorVariables,
                    ConstraintNeighbors,
                    LocalMaximumExpansionCount,
                ) {
                    Assigned.copy_from_slice(&BundleAssigned);
                    Selection.copy_from_slice(&BundleSelection);
                    return true;
                }
                if *BudgetExhausted || Deadline.WasExceeded() {
                    Assigned[SignalIndex] = false;
                    Selection[SignalIndex] = None;
                    return false;
                }
            }
            // A compact guide is selectable in this indexed witness phase
            // only together with one of its exact certified access tuples.
            // The retained tuple set is not an infeasibility proof; callers
            // may continue with the exhaustive portal relation if this phase
            // does not produce a witness.
            Assigned[SignalIndex] = false;
            Selection[SignalIndex] = None;
            continue;
        }
        match SelectionHasPoweredAccessWitness(Groups, SignalNames, Selection, Deadline, FailureNet)
        {
            Some(true) => {}
            Some(false) => {
                Assigned[SignalIndex] = false;
                Selection[SignalIndex] = None;
                continue;
            }
            None => {
                Assigned[SignalIndex] = false;
                Selection[SignalIndex] = None;
                return false;
            }
        }
        let SelectionDepth = Selection.iter().filter(|Value| Value.is_some()).count();
        let ShouldPropagate = EnforceFullArcConsistency
            && SelectionDepth >= 2
            && (!PreferGuideFactorVariables
                || SignalNames[SignalIndex].starts_with("__route_guide__:")
                || (SignalNames[SignalIndex].starts_with("__access_terminal__:")
                    && DomainCount(&Domains[SignalIndex])
                        < Groups[&SignalNames[SignalIndex]].len()));
        if ShouldPropagate
            && !(if PreferGuideFactorVariables {
                EnforceIncrementalArcConsistency(
                    SignalNames,
                    Compatibility,
                    &mut NextDomains,
                    Assigned,
                    ConstraintNeighbors,
                    &ChangedSignalIndices,
                    Deadline,
                    FailureNet,
                )
            } else {
                EnforceArcConsistency(
                    SignalNames,
                    Compatibility,
                    &mut NextDomains,
                    Assigned,
                    Deadline,
                    FailureNet,
                )
            })
        {
            ConflictSignals.clear();
            for (SelectedSignalIndex, SelectedCandidateIndex) in Selection.iter().enumerate() {
                if SelectedCandidateIndex.is_some() {
                    ConflictSignals.push(SignalNames[SelectedSignalIndex].clone());
                }
            }
            if let Some(FailingSignal) = FailureNet.as_ref() {
                ConflictSignals.push(FailingSignal.clone());
            }
            ConflictSignals.sort();
            ConflictSignals.dedup();
            let mut FailureSelection = Selection.to_vec();
            if FailureSelection
                .iter()
                .filter(|Value| Value.is_some())
                .count()
                > BestSelection.iter().filter(|Value| Value.is_some()).count()
            {
                *BestSelection = std::mem::take(&mut FailureSelection);
            }
            Assigned[SignalIndex] = false;
            Selection[SignalIndex] = None;
            if Deadline.WasExceeded() {
                return false;
            }
            continue;
        }
        if AssignIndexedCandidateDomains(
            Groups,
            SignalNames,
            Compatibility,
            &NextDomains,
            Assigned,
            Selection,
            BestSelection,
            PreferredSelection,
            ExpansionCount,
            MaximumExpansionCount,
            BudgetExhausted,
            Deadline,
            FailureDepth,
            FailureNet,
            ConflictSignals,
            ConflictResources,
            SharedExpansionCount,
            EnforceFullArcConsistency,
            PreferGuideFactorVariables,
            ConstraintNeighbors,
            LocalMaximumExpansionCount,
        ) {
            return true;
        }
        Assigned[SignalIndex] = false;
        Selection[SignalIndex] = None;
        if *BudgetExhausted || Deadline.WasExceeded() {
            return false;
        }
    }
    false
}

pub(super) struct IndexedRootBranchOutcome {
    Success: bool,
    Selection: Vec<Option<usize>>,
    BestSelection: Vec<Option<usize>>,
    ExpansionCount: usize,
    BudgetExhausted: bool,
    DeadlineExceeded: bool,
    FailureDepth: usize,
    FailureNet: Option<String>,
    ConflictSignals: Vec<String>,
    ConflictResources: Vec<usize>,
}

#[allow(clippy::too_many_arguments)]
pub(super) fn SearchIndexedGuideBranchFromState(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    SignalNames: &[String],
    Compatibility: &CandidateCompatibility,
    Domains: &[CandidateDomain],
    RootSignalIndex: usize,
    RootCandidateIndex: usize,
    MaximumExpansionCount: usize,
    Deadline: &RuntimeDeadline,
    SharedExpansionCount: &AtomicUsize,
    EnforceFullArcConsistency: bool,
    PreferGuideFactorVariables: bool,
    ConstraintNeighbors: &[Vec<usize>],
    mut Assigned: Vec<bool>,
    mut Selection: Vec<Option<usize>>,
    PreferredSelection: Vec<Option<usize>>,
    ParallelDepthRemaining: usize,
    LocalMaximumExpansionCount: usize,
) -> IndexedRootBranchOutcome {
    let mut BestSelection = Selection.clone();
    let mut ExpansionCount = 0usize;
    let mut BudgetExhausted = false;
    let mut FailureDepth = 0usize;
    let mut FailureNet = None;
    let mut ConflictSignals = Vec::new();
    let mut ConflictResources = Vec::new();
    if Deadline.Check() {
        return IndexedRootBranchOutcome {
            Success: false,
            Selection,
            BestSelection,
            ExpansionCount,
            BudgetExhausted,
            DeadlineExceeded: true,
            FailureDepth,
            FailureNet,
            ConflictSignals,
            ConflictResources,
        };
    }
    if SharedExpansionCount
        .fetch_update(AtomicOrdering::SeqCst, AtomicOrdering::SeqCst, |Value| {
            (Value < MaximumExpansionCount).then_some(Value + 1)
        })
        .is_err()
    {
        BudgetExhausted = true;
        FailureNet = Some(SignalNames[RootSignalIndex].clone());
        return IndexedRootBranchOutcome {
            Success: false,
            Selection,
            BestSelection,
            ExpansionCount,
            BudgetExhausted,
            DeadlineExceeded: false,
            FailureDepth,
            FailureNet,
            ConflictSignals,
            ConflictResources,
        };
    }
    ExpansionCount = 1;
    let mut NextDomains = Domains.to_vec();
    let mut ChangedSignalIndices = Vec::new();
    for OtherSignalIndex in 0..SignalNames.len() {
        if OtherSignalIndex == RootSignalIndex {
            continue;
        }
        let Previous = NextDomains[OtherSignalIndex].clone();
        IntersectDomain(
            &mut NextDomains[OtherSignalIndex],
            &Compatibility[RootSignalIndex][RootCandidateIndex][OtherSignalIndex],
        );
        if DomainIsEmpty(&NextDomains[OtherSignalIndex]) {
            FailureNet = Some(SignalNames[OtherSignalIndex].clone());
            BestSelection[RootSignalIndex] = Some(RootCandidateIndex);
            return IndexedRootBranchOutcome {
                Success: false,
                Selection,
                BestSelection,
                ExpansionCount,
                BudgetExhausted,
                DeadlineExceeded: Deadline.WasExceeded(),
                FailureDepth: 1,
                FailureNet,
                ConflictSignals,
                ConflictResources,
            };
        }
        if NextDomains[OtherSignalIndex] != Previous {
            ChangedSignalIndices.push(OtherSignalIndex);
        }
    }
    Assigned[RootSignalIndex] = true;
    Selection[RootSignalIndex] = Some(RootCandidateIndex);
    BestSelection = Selection.clone();
    if EnforceFullArcConsistency
        && !EnforceIncrementalArcConsistency(
            SignalNames,
            Compatibility,
            &mut NextDomains,
            &Assigned,
            ConstraintNeighbors,
            &ChangedSignalIndices,
            Deadline,
            &mut FailureNet,
        )
    {
        return IndexedRootBranchOutcome {
            Success: false,
            Selection,
            BestSelection,
            ExpansionCount,
            BudgetExhausted,
            DeadlineExceeded: Deadline.WasExceeded(),
            FailureDepth: 1,
            FailureNet,
            ConflictSignals,
            ConflictResources,
        };
    }
    if ParallelDepthRemaining > 0 {
        let NextGuideSignalIndex = (0..SignalNames.len())
            .filter(|SignalIndex| {
                !Assigned[*SignalIndex] && SignalNames[*SignalIndex].starts_with("__route_guide__:")
            })
            .min_by_key(|SignalIndex| {
                (
                    DomainCount(&NextDomains[*SignalIndex]),
                    &SignalNames[*SignalIndex],
                )
            });
        if let Some(NextGuideSignalIndex) = NextGuideSignalIndex {
            let mut CandidateIndices = (0..Groups[&SignalNames[NextGuideSignalIndex]].len())
                .filter(|CandidateIndex| {
                    DomainContains(&NextDomains[NextGuideSignalIndex], *CandidateIndex)
                })
                .map(|CandidateIndex| {
                    let CompatibleCounts = (0..SignalNames.len())
                        .filter(|OtherSignalIndex| {
                            !Assigned[*OtherSignalIndex]
                                && *OtherSignalIndex != NextGuideSignalIndex
                        })
                        .map(|OtherSignalIndex| {
                            IntersectionCount(
                                &NextDomains[OtherSignalIndex],
                                &Compatibility[NextGuideSignalIndex][CandidateIndex]
                                    [OtherSignalIndex],
                            )
                        })
                        .collect::<Vec<_>>();
                    (
                        CandidateIndex,
                        CompatibleCounts.iter().copied().min().unwrap_or(0),
                        CompatibleCounts.iter().sum::<usize>(),
                    )
                })
                .collect::<Vec<_>>();
            CandidateIndices.sort_by_key(|(CandidateIndex, MinimumCompatible, TotalCompatible)| {
                (
                    *CandidateIndex,
                    std::cmp::Reverse(*MinimumCompatible),
                    std::cmp::Reverse(*TotalCompatible),
                )
            });
            let WaveSize = RoutingThreadPool().current_num_threads().max(1);
            let mut AggregateBestSelection = BestSelection.clone();
            let mut AggregateFailureDepth = FailureDepth;
            let mut AggregateFailureNet = FailureNet.clone();
            let mut AggregateConflictSignals = ConflictSignals.clone();
            let mut AggregateConflictResources = ConflictResources.clone();
            let mut AggregateExpansionCount = ExpansionCount;
            for Wave in CandidateIndices.chunks(WaveSize) {
                let Outcomes = RoutingThreadPool().install(|| {
                    Wave.par_iter()
                        .map(|(CandidateIndex, _Minimum, _Total)| {
                            SearchIndexedGuideBranchFromState(
                                Groups,
                                SignalNames,
                                Compatibility,
                                &NextDomains,
                                NextGuideSignalIndex,
                                *CandidateIndex,
                                MaximumExpansionCount,
                                Deadline,
                                SharedExpansionCount,
                                EnforceFullArcConsistency,
                                PreferGuideFactorVariables,
                                ConstraintNeighbors,
                                Assigned.clone(),
                                Selection.clone(),
                                PreferredSelection.clone(),
                                ParallelDepthRemaining - 1,
                                LocalMaximumExpansionCount,
                            )
                        })
                        .collect::<Vec<_>>()
                });
                for Outcome in Outcomes {
                    AggregateExpansionCount =
                        AggregateExpansionCount.saturating_add(Outcome.ExpansionCount);
                    if Outcome
                        .BestSelection
                        .iter()
                        .filter(|Value| Value.is_some())
                        .count()
                        > AggregateBestSelection
                            .iter()
                            .filter(|Value| Value.is_some())
                            .count()
                    {
                        AggregateBestSelection = Outcome.BestSelection.clone();
                    }
                    if Outcome.FailureDepth > AggregateFailureDepth {
                        AggregateFailureDepth = Outcome.FailureDepth;
                        AggregateFailureNet = Outcome.FailureNet.clone();
                        AggregateConflictSignals = Outcome.ConflictSignals.clone();
                        AggregateConflictResources = Outcome.ConflictResources.clone();
                    }
                    if Outcome.DeadlineExceeded || Outcome.BudgetExhausted {
                        return IndexedRootBranchOutcome {
                            Success: false,
                            Selection,
                            BestSelection: AggregateBestSelection,
                            ExpansionCount: AggregateExpansionCount,
                            BudgetExhausted: Outcome.BudgetExhausted,
                            DeadlineExceeded: Outcome.DeadlineExceeded,
                            FailureDepth: AggregateFailureDepth,
                            FailureNet: AggregateFailureNet,
                            ConflictSignals: AggregateConflictSignals,
                            ConflictResources: AggregateConflictResources,
                        };
                    }
                    if Outcome.Success {
                        return IndexedRootBranchOutcome {
                            Success: true,
                            Selection: Outcome.Selection,
                            BestSelection: Outcome.BestSelection,
                            ExpansionCount: AggregateExpansionCount,
                            BudgetExhausted: false,
                            DeadlineExceeded: false,
                            FailureDepth: AggregateFailureDepth,
                            FailureNet: AggregateFailureNet,
                            ConflictSignals: AggregateConflictSignals,
                            ConflictResources: AggregateConflictResources,
                        };
                    }
                }
            }
            return IndexedRootBranchOutcome {
                Success: false,
                Selection,
                BestSelection: AggregateBestSelection,
                ExpansionCount: AggregateExpansionCount,
                BudgetExhausted: false,
                DeadlineExceeded: Deadline.WasExceeded(),
                FailureDepth: AggregateFailureDepth,
                FailureNet: AggregateFailureNet,
                ConflictSignals: AggregateConflictSignals,
                ConflictResources: AggregateConflictResources,
            };
        }
    }
    let Success = AssignIndexedCandidateDomains(
        Groups,
        SignalNames,
        Compatibility,
        &NextDomains,
        &mut Assigned,
        &mut Selection,
        &mut BestSelection,
        Some(&PreferredSelection),
        &mut ExpansionCount,
        MaximumExpansionCount,
        &mut BudgetExhausted,
        Deadline,
        &mut FailureDepth,
        &mut FailureNet,
        &mut ConflictSignals,
        &mut ConflictResources,
        Some(SharedExpansionCount),
        EnforceFullArcConsistency,
        PreferGuideFactorVariables,
        ConstraintNeighbors,
        Some(LocalMaximumExpansionCount.max(1)),
    );
    IndexedRootBranchOutcome {
        Success,
        Selection,
        BestSelection,
        ExpansionCount,
        BudgetExhausted,
        DeadlineExceeded: Deadline.WasExceeded(),
        FailureDepth,
        FailureNet,
        ConflictSignals,
        ConflictResources,
    }
}

pub(super) fn BuildGreedyMaximalIndexedSelection(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    SignalNames: &[String],
    Compatibility: &CandidateCompatibility,
    Domains: &[CandidateDomain],
    Deadline: &RuntimeDeadline,
    Seed: Option<(usize, usize)>,
) -> Vec<Option<usize>> {
    let mut RemainingDomains = Domains.to_vec();
    let mut Completed = vec![false; SignalNames.len()];
    let mut Selection = vec![None; SignalNames.len()];
    if let Some((SignalIndex, CandidateIndex)) = Seed {
        if SignalIndex >= SignalNames.len()
            || CandidateIndex >= Compatibility[SignalIndex].len()
            || !DomainContains(&RemainingDomains[SignalIndex], CandidateIndex)
        {
            return Selection;
        }
        Completed[SignalIndex] = true;
        Selection[SignalIndex] = Some(CandidateIndex);
        for OtherSignalIndex in 0..SignalNames.len() {
            if OtherSignalIndex == SignalIndex {
                continue;
            }
            IntersectDomain(
                &mut RemainingDomains[OtherSignalIndex],
                &Compatibility[SignalIndex][CandidateIndex][OtherSignalIndex],
            );
        }
    }
    while Completed.iter().any(|Value| !*Value) {
        if Deadline.Check() {
            break;
        }
        let Some(SignalIndex) = (0..SignalNames.len())
            .filter(|Index| !Completed[*Index])
            .min_by_key(|Index| {
                (
                    usize::from(!IsDetachedInternalGuideVariable(
                        Groups,
                        &SignalNames[*Index],
                    )),
                    DomainCount(&RemainingDomains[*Index]),
                    &SignalNames[*Index],
                )
            })
        else {
            break;
        };
        Completed[SignalIndex] = true;
        if DomainIsEmpty(&RemainingDomains[SignalIndex]) {
            continue;
        }
        let CandidateCount = Compatibility[SignalIndex].len();
        let CandidateChoices = (0..CandidateCount)
            .filter(|CandidateIndex| {
                DomainContains(&RemainingDomains[SignalIndex], *CandidateIndex)
            })
            .map(|CandidateIndex| {
                let CompatibleCounts = (0..SignalNames.len())
                    .filter(|OtherSignalIndex| {
                        !Completed[*OtherSignalIndex] && *OtherSignalIndex != SignalIndex
                    })
                    .map(|OtherSignalIndex| {
                        IntersectionCount(
                            &RemainingDomains[OtherSignalIndex],
                            &Compatibility[SignalIndex][CandidateIndex][OtherSignalIndex],
                        )
                    })
                    .collect::<Vec<_>>();
                (
                    CandidateIndex,
                    CompatibleCounts.iter().copied().min().unwrap_or(0),
                    CompatibleCounts.iter().sum::<usize>(),
                )
            })
            .collect::<Vec<_>>();
        let SelectedCandidateIndex = if SignalNames[SignalIndex].starts_with("__route_guide__:") {
            CandidateChoices
                .iter()
                .min_by_key(|(CandidateIndex, MinimumCompatible, TotalCompatible)| {
                    (
                        *CandidateIndex,
                        std::cmp::Reverse(*MinimumCompatible),
                        std::cmp::Reverse(*TotalCompatible),
                    )
                })
                .map(|Value| Value.0)
        } else {
            CandidateChoices
                .iter()
                .max_by_key(|(CandidateIndex, MinimumCompatible, TotalCompatible)| {
                    (
                        *MinimumCompatible,
                        *TotalCompatible,
                        std::cmp::Reverse(*CandidateIndex),
                    )
                })
                .map(|Value| Value.0)
        };
        let Some(CandidateIndex) = SelectedCandidateIndex else {
            continue;
        };
        Selection[SignalIndex] = Some(CandidateIndex);
        for OtherSignalIndex in 0..SignalNames.len() {
            if Completed[OtherSignalIndex] {
                continue;
            }
            IntersectDomain(
                &mut RemainingDomains[OtherSignalIndex],
                &Compatibility[SignalIndex][CandidateIndex][OtherSignalIndex],
            );
        }
    }
    Selection
}

pub(super) fn BuildGreedyRepairIndexedSelection(
    SignalNames: &[String],
    Compatibility: &CandidateCompatibility,
    Domains: &[CandidateDomain],
    Deadline: &RuntimeDeadline,
    BaseSelection: &[Option<usize>],
    PivotSignalIndex: usize,
    PivotCandidateIndex: usize,
) -> Vec<Option<usize>> {
    if PivotSignalIndex >= SignalNames.len()
        || PivotCandidateIndex >= Compatibility[PivotSignalIndex].len()
        || !DomainContains(&Domains[PivotSignalIndex], PivotCandidateIndex)
    {
        return vec![None; SignalNames.len()];
    }
    let mut RemainingDomains = Domains.to_vec();
    let mut Completed = vec![false; SignalNames.len()];
    let mut Selection = vec![None; SignalNames.len()];
    let mut Retained = BaseSelection
        .iter()
        .enumerate()
        .filter_map(|(SignalIndex, CandidateIndex)| {
            let CandidateIndex = (*CandidateIndex)?;
            (SignalIndex != PivotSignalIndex
                && DomainContains(
                    &Compatibility[PivotSignalIndex][PivotCandidateIndex][SignalIndex],
                    CandidateIndex,
                ))
            .then_some((SignalIndex, CandidateIndex))
        })
        .collect::<Vec<_>>();
    Retained.push((PivotSignalIndex, PivotCandidateIndex));
    Retained.sort_unstable();
    for (SignalIndex, CandidateIndex) in Retained {
        if Deadline.Check() || !DomainContains(&RemainingDomains[SignalIndex], CandidateIndex) {
            continue;
        }
        Completed[SignalIndex] = true;
        Selection[SignalIndex] = Some(CandidateIndex);
        for OtherSignalIndex in 0..SignalNames.len() {
            if OtherSignalIndex == SignalIndex || Completed[OtherSignalIndex] {
                continue;
            }
            IntersectDomain(
                &mut RemainingDomains[OtherSignalIndex],
                &Compatibility[SignalIndex][CandidateIndex][OtherSignalIndex],
            );
        }
    }
    while Completed.iter().any(|Value| !*Value) {
        if Deadline.Check() {
            break;
        }
        let Some(SignalIndex) = (0..SignalNames.len())
            .filter(|Index| !Completed[*Index])
            .min_by_key(|Index| (DomainCount(&RemainingDomains[*Index]), &SignalNames[*Index]))
        else {
            break;
        };
        Completed[SignalIndex] = true;
        if DomainIsEmpty(&RemainingDomains[SignalIndex]) {
            continue;
        }
        let SelectedCandidateIndex = (0..Compatibility[SignalIndex].len())
            .filter(|CandidateIndex| {
                DomainContains(&RemainingDomains[SignalIndex], *CandidateIndex)
            })
            .map(|CandidateIndex| {
                let CompatibleCounts = (0..SignalNames.len())
                    .filter(|OtherSignalIndex| {
                        !Completed[*OtherSignalIndex] && *OtherSignalIndex != SignalIndex
                    })
                    .map(|OtherSignalIndex| {
                        IntersectionCount(
                            &RemainingDomains[OtherSignalIndex],
                            &Compatibility[SignalIndex][CandidateIndex][OtherSignalIndex],
                        )
                    })
                    .collect::<Vec<_>>();
                (
                    CandidateIndex,
                    CompatibleCounts.iter().copied().min().unwrap_or(0),
                    CompatibleCounts.iter().sum::<usize>(),
                )
            })
            .max_by_key(|(CandidateIndex, MinimumCompatible, TotalCompatible)| {
                (
                    *MinimumCompatible,
                    *TotalCompatible,
                    std::cmp::Reverse(*CandidateIndex),
                )
            })
            .map(|Value| Value.0);
        let Some(CandidateIndex) = SelectedCandidateIndex else {
            continue;
        };
        Selection[SignalIndex] = Some(CandidateIndex);
        for OtherSignalIndex in 0..SignalNames.len() {
            if Completed[OtherSignalIndex] {
                continue;
            }
            IntersectDomain(
                &mut RemainingDomains[OtherSignalIndex],
                &Compatibility[SignalIndex][CandidateIndex][OtherSignalIndex],
            );
        }
    }
    Selection
}

pub(super) fn LayeredGuideIndexForAccessVariable(
    SignalNames: &[String],
    AccessSignalIndex: usize,
) -> Option<usize> {
    let LogicalSignal = SignalNames[AccessSignalIndex]
        .strip_prefix("__access_terminal__:")?
        .split('@')
        .next()?;
    let GuideVariable = format!("__route_guide__:{LogicalSignal}");
    SignalNames
        .iter()
        .position(|Variable| Variable == &GuideVariable)
}

#[allow(clippy::too_many_arguments)]
pub(super) fn SearchBestLayeredGuideAccessBundle(
    Compatibility: &CandidateCompatibility,
    Domains: &[CandidateDomain],
    Selection: &[Option<usize>],
    GuideSignalIndex: usize,
    GuideCandidateIndex: usize,
    AccessSignalIndices: &[usize],
    AccessOffset: usize,
    CurrentCandidates: &mut Vec<usize>,
    Best: &mut Option<(usize, Vec<usize>)>,
) {
    let Some(AccessSignalIndex) = AccessSignalIndices.get(AccessOffset).copied() else {
        let ConflictCount = AccessSignalIndices
            .iter()
            .zip(CurrentCandidates.iter())
            .map(|(SelectedAccessIndex, SelectedAccessCandidateIndex)| {
                Selection
                    .iter()
                    .enumerate()
                    .filter(|(OtherSignalIndex, OtherCandidateIndex)| {
                        *OtherSignalIndex != GuideSignalIndex
                            && !AccessSignalIndices.contains(OtherSignalIndex)
                            && OtherCandidateIndex.is_some_and(|OtherCandidateIndex| {
                                !DomainContains(
                                    &Compatibility[*SelectedAccessIndex]
                                        [*SelectedAccessCandidateIndex][*OtherSignalIndex],
                                    OtherCandidateIndex,
                                )
                            })
                    })
                    .count()
            })
            .sum::<usize>();
        let CandidateTuple = CurrentCandidates.clone();
        if Best.as_ref().is_none_or(|(BestConflictCount, BestTuple)| {
            (ConflictCount, &CandidateTuple) < (*BestConflictCount, BestTuple)
        }) {
            *Best = Some((ConflictCount, CandidateTuple));
        }
        return;
    };
    for CandidateIndex in 0..Compatibility[AccessSignalIndex].len() {
        if !DomainContains(&Domains[AccessSignalIndex], CandidateIndex)
            || !DomainContains(
                &Compatibility[GuideSignalIndex][GuideCandidateIndex][AccessSignalIndex],
                CandidateIndex,
            )
            || AccessSignalIndices[..AccessOffset]
                .iter()
                .zip(CurrentCandidates.iter())
                .any(|(PreviousSignalIndex, PreviousCandidateIndex)| {
                    !DomainContains(
                        &Compatibility[AccessSignalIndex][CandidateIndex][*PreviousSignalIndex],
                        *PreviousCandidateIndex,
                    )
                })
        {
            continue;
        }
        CurrentCandidates.push(CandidateIndex);
        SearchBestLayeredGuideAccessBundle(
            Compatibility,
            Domains,
            Selection,
            GuideSignalIndex,
            GuideCandidateIndex,
            AccessSignalIndices,
            AccessOffset + 1,
            CurrentCandidates,
            Best,
        );
        CurrentCandidates.pop();
    }
}

pub(super) fn SynchronizeLayeredGuideAccessSelection(
    SignalNames: &[String],
    Compatibility: &CandidateCompatibility,
    Domains: &[CandidateDomain],
    Selection: &mut [Option<usize>],
    GuideSignalIndex: usize,
) -> bool {
    let Some(GuideCandidateIndex) = Selection[GuideSignalIndex] else {
        return false;
    };
    let AccessSignalIndices = (0..SignalNames.len())
        .filter(|SignalIndex| {
            LayeredGuideIndexForAccessVariable(SignalNames, *SignalIndex) == Some(GuideSignalIndex)
        })
        .collect::<Vec<_>>();
    let mut Best = None::<(usize, Vec<usize>)>;
    SearchBestLayeredGuideAccessBundle(
        Compatibility,
        Domains,
        Selection,
        GuideSignalIndex,
        GuideCandidateIndex,
        &AccessSignalIndices,
        0,
        &mut Vec::new(),
        &mut Best,
    );
    let Some((_ConflictCount, SelectedCandidates)) = Best else {
        return false;
    };
    for (AccessSignalIndex, CandidateIndex) in AccessSignalIndices.iter().zip(SelectedCandidates) {
        Selection[*AccessSignalIndex] = Some(CandidateIndex);
    }
    true
}

pub(super) fn BuildBoundedMinConflictIndexedSelection(
    SignalNames: &[String],
    Compatibility: &CandidateCompatibility,
    Domains: &[CandidateDomain],
    BaseSelection: &[Option<usize>],
    SharedExpansionCount: &AtomicUsize,
    MaximumExpansionCount: usize,
    Deadline: &RuntimeDeadline,
    MaximumMoves: usize,
) -> (Option<Vec<Option<usize>>>, usize, usize, bool) {
    let SignalCount = Domains.len();
    let mut Selection = BaseSelection.to_vec();
    let mut RandomState = 0x9e37_79b9_7f4a_7c15u64;
    let mut NextRandom = |UpperBound: usize| {
        RandomState = RandomState
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        ((RandomState >> 32) as usize) % UpperBound.max(1)
    };
    for SignalIndex in 0..SignalCount {
        if Selection[SignalIndex].is_some() {
            continue;
        }
        let Existing = Selection
            .iter()
            .enumerate()
            .filter_map(|(OtherSignalIndex, CandidateIndex)| {
                CandidateIndex.map(|CandidateIndex| (OtherSignalIndex, CandidateIndex))
            })
            .collect::<Vec<_>>();
        Selection[SignalIndex] = (0..Compatibility[SignalIndex].len())
            .filter(|CandidateIndex| DomainContains(&Domains[SignalIndex], *CandidateIndex))
            .min_by_key(|CandidateIndex| {
                let ConflictCount = Existing
                    .iter()
                    .filter(|(OtherSignalIndex, OtherCandidateIndex)| {
                        !DomainContains(
                            &Compatibility[SignalIndex][*CandidateIndex][*OtherSignalIndex],
                            *OtherCandidateIndex,
                        )
                    })
                    .count();
                (ConflictCount, *CandidateIndex)
            });
    }
    if Selection.iter().any(Option::is_none) {
        return (None, 0, usize::MAX, false);
    }
    for GuideSignalIndex in 0..SignalCount {
        if SignalNames[GuideSignalIndex].starts_with("__route_guide__:") {
            let _ = SynchronizeLayeredGuideAccessSelection(
                SignalNames,
                Compatibility,
                Domains,
                &mut Selection,
                GuideSignalIndex,
            );
        }
    }
    let mut MoveCount = 0usize;
    let mut BestConflictCount = usize::MAX;
    let mut BestLocalSelection = Selection.clone();
    let mut MovesSinceImprovement = 0usize;
    let mut ConflictWeights = vec![1usize; SignalCount.saturating_mul(SignalCount)];
    for FirstSignalIndex in 0..SignalCount {
        for SecondSignalIndex in (FirstSignalIndex + 1)..SignalCount {
            let FirstIsGuide = SignalNames[FirstSignalIndex].starts_with("__route_guide__:");
            let SecondIsGuide = SignalNames[SecondSignalIndex].starts_with("__route_guide__:");
            let FirstIsAccess = SignalNames[FirstSignalIndex].starts_with("__access_terminal__:");
            let SecondIsAccess = SignalNames[SecondSignalIndex].starts_with("__access_terminal__:");
            if (FirstIsGuide && SecondIsAccess) || (FirstIsAccess && SecondIsGuide) {
                ConflictWeights[FirstSignalIndex * SignalCount + SecondSignalIndex] = 64;
                ConflictWeights[SecondSignalIndex * SignalCount + FirstSignalIndex] = 64;
            }
        }
    }
    while MoveCount < MaximumMoves && !Deadline.Check() {
        let ConcreteSelection = Selection
            .iter()
            .map(|CandidateIndex| CandidateIndex.expect("complete local-search selection"))
            .collect::<Vec<_>>();
        let mut ConflictsBySignal = vec![0usize; SignalCount];
        let mut WeightedConflictsBySignal = vec![0usize; SignalCount];
        for FirstSignalIndex in 0..SignalCount {
            for SecondSignalIndex in (FirstSignalIndex + 1)..SignalCount {
                if !DomainContains(
                    &Compatibility[FirstSignalIndex][ConcreteSelection[FirstSignalIndex]]
                        [SecondSignalIndex],
                    ConcreteSelection[SecondSignalIndex],
                ) {
                    ConflictsBySignal[FirstSignalIndex] += 1;
                    ConflictsBySignal[SecondSignalIndex] += 1;
                    let Weight =
                        ConflictWeights[FirstSignalIndex * SignalCount + SecondSignalIndex];
                    WeightedConflictsBySignal[FirstSignalIndex] += Weight;
                    WeightedConflictsBySignal[SecondSignalIndex] += Weight;
                }
            }
        }
        let TotalConflictCount = ConflictsBySignal.iter().sum::<usize>() / 2;
        if TotalConflictCount < BestConflictCount {
            BestConflictCount = TotalConflictCount;
            BestLocalSelection = Selection.clone();
            MovesSinceImprovement = 0;
        } else {
            MovesSinceImprovement += 1;
        }
        if TotalConflictCount == 0 {
            return (Some(Selection), MoveCount, 0, false);
        }
        if MovesSinceImprovement >= 1_000 {
            Selection = BestLocalSelection.clone();
            for SignalIndex in 0..SignalCount {
                let CandidateIndices = (0..Compatibility[SignalIndex].len())
                    .filter(|CandidateIndex| DomainContains(&Domains[SignalIndex], *CandidateIndex))
                    .collect::<Vec<_>>();
                if CandidateIndices.len() > 1 && NextRandom(4) == 0 {
                    Selection[SignalIndex] =
                        Some(CandidateIndices[NextRandom(CandidateIndices.len())]);
                }
            }
            MovesSinceImprovement = 0;
            continue;
        }
        let MutableConflictedSignals = (0..SignalCount)
            .filter(|SignalIndex| ConflictsBySignal[*SignalIndex] > 0)
            .filter(|SignalIndex| DomainCount(&Domains[*SignalIndex]) > 1)
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        if MutableConflictedSignals.is_empty() {
            break;
        }
        let SignalIndex = if MoveCount % 7 == 0 {
            MutableConflictedSignals[NextRandom(MutableConflictedSignals.len())]
        } else {
            *MutableConflictedSignals
                .iter()
                .max_by_key(|SignalIndex| {
                    (
                        WeightedConflictsBySignal[**SignalIndex],
                        ConflictsBySignal[**SignalIndex],
                        std::cmp::Reverse(**SignalIndex),
                    )
                })
                .expect("nonempty conflicted signals")
        };
        let mut CandidateScores = (0..Compatibility[SignalIndex].len())
            .filter(|CandidateIndex| DomainContains(&Domains[SignalIndex], *CandidateIndex))
            .filter_map(|CandidateIndex| {
                let mut TrialSelection = Selection.clone();
                TrialSelection[SignalIndex] = Some(CandidateIndex);
                let IsGuide = SignalNames[SignalIndex].starts_with("__route_guide__:");
                if IsGuide
                    && !SynchronizeLayeredGuideAccessSelection(
                        SignalNames,
                        Compatibility,
                        Domains,
                        &mut TrialSelection,
                        SignalIndex,
                    )
                {
                    return None;
                }
                let ChangedSignalIndices = if IsGuide {
                    (0..SignalCount)
                        .filter(|OtherSignalIndex| {
                            *OtherSignalIndex == SignalIndex
                                || LayeredGuideIndexForAccessVariable(
                                    SignalNames,
                                    *OtherSignalIndex,
                                ) == Some(SignalIndex)
                        })
                        .collect::<Vec<_>>()
                } else {
                    vec![SignalIndex]
                };
                let WeightedConflictCount = ChangedSignalIndices
                    .iter()
                    .enumerate()
                    .map(|(ChangedOffset, ChangedSignalIndex)| {
                        let ChangedCandidateIndex = TrialSelection[*ChangedSignalIndex]
                            .expect("atomic layered bundle has every exact value");
                        TrialSelection
                            .iter()
                            .enumerate()
                            .filter_map(|(OtherSignalIndex, OtherCandidateIndex)| {
                                let Some(OtherCandidateIndex) = *OtherCandidateIndex else {
                                    return None;
                                };
                                if OtherSignalIndex == *ChangedSignalIndex
                                    || ChangedSignalIndices[..ChangedOffset]
                                        .contains(&OtherSignalIndex)
                                {
                                    return None;
                                }
                                (!DomainContains(
                                    &Compatibility[*ChangedSignalIndex][ChangedCandidateIndex]
                                        [OtherSignalIndex],
                                    OtherCandidateIndex,
                                ))
                                .then_some(
                                    ConflictWeights
                                        [*ChangedSignalIndex * SignalCount + OtherSignalIndex],
                                )
                            })
                            .sum::<usize>()
                    })
                    .sum::<usize>();
                Some((CandidateIndex, WeightedConflictCount, TrialSelection))
            })
            .collect::<Vec<_>>();
        CandidateScores.sort_by_key(|(CandidateIndex, ConflictCount, _Selection)| {
            (*ConflictCount, *CandidateIndex)
        });
        if CandidateScores.is_empty() {
            break;
        }
        let MinimumConflictCount = CandidateScores[0].1;
        let MinimumChoiceCount = CandidateScores
            .iter()
            .take_while(|(_CandidateIndex, ConflictCount, _Selection)| {
                *ConflictCount == MinimumConflictCount
            })
            .count();
        let CurrentWeightedConflictCount = WeightedConflictsBySignal[SignalIndex];
        if MinimumConflictCount >= CurrentWeightedConflictCount {
            for OtherSignalIndex in 0..SignalCount {
                if OtherSignalIndex == SignalIndex
                    || DomainContains(
                        &Compatibility[SignalIndex][ConcreteSelection[SignalIndex]]
                            [OtherSignalIndex],
                        ConcreteSelection[OtherSignalIndex],
                    )
                {
                    continue;
                }
                ConflictWeights[SignalIndex * SignalCount + OtherSignalIndex] += 1;
                ConflictWeights[OtherSignalIndex * SignalCount + SignalIndex] += 1;
            }
        }
        let SelectedCandidate = if MoveCount % 13 == 0 {
            &CandidateScores[NextRandom(CandidateScores.len())]
        } else {
            &CandidateScores[NextRandom(MinimumChoiceCount)]
        };
        if SharedExpansionCount
            .fetch_update(AtomicOrdering::SeqCst, AtomicOrdering::SeqCst, |Value| {
                (Value < MaximumExpansionCount).then_some(Value + 1)
            })
            .is_err()
        {
            return (Some(BestLocalSelection), MoveCount, BestConflictCount, true);
        }
        Selection = SelectedCandidate.2.clone();
        MoveCount += 1;
    }
    (
        Some(BestLocalSelection),
        MoveCount,
        BestConflictCount,
        false,
    )
}

#[allow(clippy::too_many_arguments)]
pub(super) fn SearchMaximumPartialIndexedSelection(
    SignalNames: &[String],
    Compatibility: &CandidateCompatibility,
    Domains: &[CandidateDomain],
    Completed: &mut [bool],
    Selection: &mut [Option<usize>],
    BestSelection: &mut Vec<Option<usize>>,
    ExpansionCount: &mut usize,
    MaximumExpansionCount: usize,
    Deadline: &RuntimeDeadline,
) {
    if *ExpansionCount >= MaximumExpansionCount || Deadline.Check() {
        return;
    }
    let SelectionCount = Selection.iter().filter(|Value| Value.is_some()).count();
    let BestCount = BestSelection.iter().filter(|Value| Value.is_some()).count();
    if SelectionCount > BestCount {
        *BestSelection = Selection.to_vec();
    }
    let RemainingCount = Completed.iter().filter(|Value| !**Value).count();
    if RemainingCount == 0 || SelectionCount + RemainingCount <= BestCount {
        return;
    }
    let Some(SignalIndex) = (0..SignalNames.len())
        .filter(|Index| !Completed[*Index])
        .min_by_key(|Index| (DomainCount(&Domains[*Index]), &SignalNames[*Index]))
    else {
        return;
    };
    Completed[SignalIndex] = true;
    let CandidateCount = Compatibility[SignalIndex].len();
    let mut CandidateIndices = (0..CandidateCount)
        .filter(|CandidateIndex| DomainContains(&Domains[SignalIndex], *CandidateIndex))
        .map(|CandidateIndex| {
            let CompatibleCounts = (0..SignalNames.len())
                .filter(|OtherSignalIndex| !Completed[*OtherSignalIndex])
                .map(|OtherSignalIndex| {
                    IntersectionCount(
                        &Domains[OtherSignalIndex],
                        &Compatibility[SignalIndex][CandidateIndex][OtherSignalIndex],
                    )
                })
                .collect::<Vec<_>>();
            (
                CandidateIndex,
                CompatibleCounts.iter().copied().min().unwrap_or(0),
                CompatibleCounts.iter().sum::<usize>(),
            )
        })
        .collect::<Vec<_>>();
    CandidateIndices.sort_by(
        |(FirstIndex, FirstMinimum, FirstTotal), (SecondIndex, SecondMinimum, SecondTotal)| {
            (
                std::cmp::Reverse(*FirstMinimum),
                std::cmp::Reverse(*FirstTotal),
                *FirstIndex,
            )
                .cmp(&(
                    std::cmp::Reverse(*SecondMinimum),
                    std::cmp::Reverse(*SecondTotal),
                    *SecondIndex,
                ))
        },
    );
    for (CandidateIndex, _MinimumCompatible, _TotalCompatible) in CandidateIndices {
        if *ExpansionCount >= MaximumExpansionCount || Deadline.Check() {
            break;
        }
        *ExpansionCount += 1;
        let mut NextDomains = Domains.to_vec();
        for OtherSignalIndex in 0..SignalNames.len() {
            if Completed[OtherSignalIndex] {
                continue;
            }
            IntersectDomain(
                &mut NextDomains[OtherSignalIndex],
                &Compatibility[SignalIndex][CandidateIndex][OtherSignalIndex],
            );
        }
        Selection[SignalIndex] = Some(CandidateIndex);
        SearchMaximumPartialIndexedSelection(
            SignalNames,
            Compatibility,
            &NextDomains,
            Completed,
            Selection,
            BestSelection,
            ExpansionCount,
            MaximumExpansionCount,
            Deadline,
        );
        Selection[SignalIndex] = None;
    }
    SearchMaximumPartialIndexedSelection(
        SignalNames,
        Compatibility,
        Domains,
        Completed,
        Selection,
        BestSelection,
        ExpansionCount,
        MaximumExpansionCount,
        Deadline,
    );
    Completed[SignalIndex] = false;
}

pub(super) fn BuildMaximumPartialIndexedSelection(
    SignalNames: &[String],
    Compatibility: &CandidateCompatibility,
    Domains: &[CandidateDomain],
    InitialBestSelection: Vec<Option<usize>>,
    MaximumExpansionCount: usize,
    Deadline: &RuntimeDeadline,
) -> Vec<Option<usize>> {
    let mut Completed = vec![false; SignalNames.len()];
    let mut Selection = vec![None; SignalNames.len()];
    let mut BestSelection = InitialBestSelection;
    let mut ExpansionCount = 0usize;
    SearchMaximumPartialIndexedSelection(
        SignalNames,
        Compatibility,
        Domains,
        &mut Completed,
        &mut Selection,
        &mut BestSelection,
        &mut ExpansionCount,
        MaximumExpansionCount,
        Deadline,
    );
    BestSelection
}

pub(super) fn TryAssignIndexedCandidates(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    Domains: &BTreeMap<String, Vec<usize>>,
    Selected: &mut Vec<(String, String)>,
    ExpansionCount: &mut usize,
    MaximumExpansionCount: usize,
    BudgetExhausted: &mut bool,
    Deadline: &RuntimeDeadline,
    FailureNet: &mut Option<String>,
    ConflictSignals: &mut Vec<String>,
    ConflictResources: &mut Vec<usize>,
    PairwiseIncompatibleSignals: &mut Vec<(String, String)>,
    PairwiseCompatibilityComplete: &mut bool,
    SharedExpansionCount: Option<&AtomicUsize>,
    CrossAirByWire: Option<&[Vec<(usize, usize)>]>,
) -> Option<bool> {
    let StartedAt = std::time::Instant::now();
    const MAXIMUM_COMPATIBILITY_WORDS: usize = 16_000_000;
    let SignalNames = Domains.keys().cloned().collect::<Vec<_>>();
    let DomainWordCounts = SignalNames
        .iter()
        .map(|Signal| DomainWordCount(Groups[Signal].len()))
        .collect::<Vec<_>>();
    let WordsPerCandidate = DomainWordCounts.iter().sum::<usize>();
    let TotalCandidateCount = SignalNames
        .iter()
        .map(|Signal| Groups[Signal].len())
        .sum::<usize>();
    if TotalCandidateCount
        .checked_mul(WordsPerCandidate)
        .is_none_or(|Value| Value > MAXIMUM_COMPATIBILITY_WORDS)
    {
        return None;
    }

    let mut IndexedDomains = SignalNames
        .iter()
        .enumerate()
        .map(|(SignalIndex, _Signal)| vec![0u64; DomainWordCounts[SignalIndex]])
        .collect::<Vec<_>>();
    for (SignalIndex, Signal) in SignalNames.iter().enumerate() {
        for CandidateIndex in &Domains[Signal] {
            SetDomainBit(&mut IndexedDomains[SignalIndex], *CandidateIndex);
        }
    }
    let SparseAccessCompatibility = BuildSparseAccessCompatibility(
        Groups,
        &SignalNames,
        Domains,
        &IndexedDomains,
        CrossAirByWire,
        Deadline,
    );
    let PreferGuideFactorVariables = SignalNames.iter().all(|Signal| {
        Signal.starts_with("__access_terminal__:")
            || Signal.starts_with("__route_guide__:")
            || Signal.starts_with("__base_claim__:")
    });
    // Factorized catalogs propagate globally when a guide fixes the portal
    // tuple for one complete signal, while the immediately following exact
    // stub choices use cheap forward checking.  Ordinary domains propagate
    // after every decision.
    let EnforceFullArcConsistency = true;
    let Compatibility = if let Some(Value) = SparseAccessCompatibility {
        Value
    } else {
        let mut Value = SignalNames
            .iter()
            .map(|Signal| {
                (0..Groups[Signal].len())
                    .map(|_| {
                        DomainWordCounts
                            .iter()
                            .map(|WordCount| Arc::new(vec![0u64; *WordCount]))
                            .collect::<Vec<_>>()
                    })
                    .collect::<Vec<_>>()
            })
            .collect::<CandidateCompatibility>();
        let SignalPairs = (0..SignalNames.len())
            .flat_map(|FirstSignalIndex| {
                ((FirstSignalIndex + 1)..SignalNames.len())
                    .map(move |SecondSignalIndex| (FirstSignalIndex, SecondSignalIndex))
            })
            .collect::<Vec<_>>();
        let PairCompatibility = RoutingThreadPool().install(|| {
            SignalPairs
                .into_par_iter()
                .map(|(FirstSignalIndex, SecondSignalIndex)| {
                    let mut CompatiblePairs = Vec::new();
                    let mut PairCount = 0usize;
                    for FirstCandidateIndex in &Domains[&SignalNames[FirstSignalIndex]] {
                        for SecondCandidateIndex in &Domains[&SignalNames[SecondSignalIndex]] {
                            PairCount += 1;
                            if PairCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                                return (FirstSignalIndex, SecondSignalIndex, None);
                            }
                            let First =
                                &Groups[&SignalNames[FirstSignalIndex]][*FirstCandidateIndex];
                            let Second =
                                &Groups[&SignalNames[SecondSignalIndex]][*SecondCandidateIndex];
                            if TemplatesAreCompatible(First, Second)
                                && (if First.OwnerSignal == Second.OwnerSignal {
                                    !First.Claims.SameOwnerConflicts(&Second.Claims)
                                } else {
                                    !First.Claims.Conflicts(&Second.Claims)
                                })
                            {
                                CompatiblePairs.push((*FirstCandidateIndex, *SecondCandidateIndex));
                            }
                        }
                    }
                    (FirstSignalIndex, SecondSignalIndex, Some(CompatiblePairs))
                })
                .collect::<Vec<_>>()
        });
        if Deadline.Check()
            || PairCompatibility
                .iter()
                .any(|(_First, _Second, Values)| Values.is_none())
        {
            return Some(false);
        }
        for (FirstSignalIndex, SecondSignalIndex, CompatiblePairs) in PairCompatibility {
            let CompatiblePairs =
                CompatiblePairs.expect("deadline-free compatibility partition must be complete");
            for (FirstCandidateIndex, SecondCandidateIndex) in CompatiblePairs {
                SetDomainBit(
                    Arc::make_mut(
                        &mut Value[FirstSignalIndex][FirstCandidateIndex][SecondSignalIndex],
                    ),
                    SecondCandidateIndex,
                );
                SetDomainBit(
                    Arc::make_mut(
                        &mut Value[SecondSignalIndex][SecondCandidateIndex][FirstSignalIndex],
                    ),
                    FirstCandidateIndex,
                );
            }
        }
        Value
    };
    if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE").as_deref() == Ok("1") {
        eprintln!(
            "[debug] indexed assignment compatibility_ms={}",
            StartedAt.elapsed().as_millis(),
        );
    }
    if Deadline.Check() {
        return Some(false);
    }
    PairwiseIncompatibleSignals.clear();
    for FirstSignalIndex in 0..SignalNames.len() {
        for SecondSignalIndex in (FirstSignalIndex + 1)..SignalNames.len() {
            let HasCompatiblePair =
                Domains[&SignalNames[FirstSignalIndex]]
                    .iter()
                    .any(|FirstCandidateIndex| {
                        Domains[&SignalNames[SecondSignalIndex]].iter().any(
                            |SecondCandidateIndex| {
                                DomainContains(
                                    &Compatibility[FirstSignalIndex][*FirstCandidateIndex]
                                        [SecondSignalIndex],
                                    *SecondCandidateIndex,
                                )
                            },
                        )
                    });
            if !HasCompatiblePair {
                PairwiseIncompatibleSignals.push((
                    SignalNames[FirstSignalIndex].clone(),
                    SignalNames[SecondSignalIndex].clone(),
                ));
            }
        }
    }
    *PairwiseCompatibilityComplete = true;
    if Deadline.Check() {
        return Some(false);
    }

    let mut Assigned = vec![false; SignalNames.len()];
    let mut Selection = vec![None; SignalNames.len()];
    let mut BestSelection = Selection.clone();
    let mut FailureDepth = 0usize;
    let ConstraintNeighbors = (0..SignalNames.len())
        .map(|FirstSignalIndex| {
            (0..SignalNames.len())
                .filter(|SecondSignalIndex| {
                    if *SecondSignalIndex == FirstSignalIndex {
                        return false;
                    }
                    Domains[&SignalNames[FirstSignalIndex]]
                        .iter()
                        .any(|FirstCandidateIndex| {
                            Compatibility[FirstSignalIndex][*FirstCandidateIndex]
                                [*SecondSignalIndex]
                                .as_ref()
                                != &IndexedDomains[*SecondSignalIndex]
                        })
                })
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    let InitialArcConsistencyStartedAt = std::time::Instant::now();
    let InitialIndexedDomains = std::env::var_os("RCS_DEBUG_NATIVE_SELECTOR_ORDER")
        .is_some()
        .then(|| IndexedDomains.clone());
    if PreferGuideFactorVariables
        && !EnforceArcConsistency(
            &SignalNames,
            &Compatibility,
            &mut IndexedDomains,
            &Assigned,
            Deadline,
            FailureNet,
        )
    {
        return Some(false);
    }
    if std::env::var_os("RCS_DEBUG_NATIVE_SELECTOR_ORDER").is_some() {
        let InitialIndexedDomains = InitialIndexedDomains
            .as_ref()
            .expect("selector-order diagnostics retained the initial domains");
        for (SignalIndex, Signal) in SignalNames.iter().enumerate() {
            let DebugSignal = std::env::var("RCS_DEBUG_NATIVE_SELECTOR_SIGNAL").ok();
            if DebugSignal.is_none() && !IsDetachedInternalGuideVariable(Groups, Signal) {
                continue;
            }
            if DebugSignal.is_some_and(|DebugSignal| DebugSignal != *Signal) {
                continue;
            }
            eprintln!(
                "native selector detached domain variable={} values={:?}",
                Signal,
                Groups[Signal]
                    .iter()
                    .enumerate()
                    .filter(|(CandidateIndex, _Candidate)| {
                        DomainContains(&IndexedDomains[SignalIndex], *CandidateIndex)
                    })
                    .take(32)
                    .map(|(CandidateIndex, Candidate)| (
                        CandidateIndex,
                        Candidate.CandidateId.as_str(),
                        Candidate.MaterialCost,
                        Candidate.FootprintGrowth,
                    ))
                    .collect::<Vec<_>>(),
            );
            eprintln!(
                "native selector detached removed variable={} values={:?}",
                Signal,
                Groups[Signal]
                    .iter()
                    .enumerate()
                    .filter(|(CandidateIndex, _Candidate)| {
                        DomainContains(&InitialIndexedDomains[SignalIndex], *CandidateIndex)
                            && !DomainContains(&IndexedDomains[SignalIndex], *CandidateIndex)
                    })
                    .take(24)
                    .map(|(CandidateIndex, Candidate)| {
                        let BlockingVariables = (0..SignalNames.len())
                            .filter(|OtherSignalIndex| *OtherSignalIndex != SignalIndex)
                            .filter(|OtherSignalIndex| {
                                IntersectionCount(
                                    &IndexedDomains[*OtherSignalIndex],
                                    &Compatibility[SignalIndex][CandidateIndex][*OtherSignalIndex],
                                ) == 0
                            })
                            .map(|OtherSignalIndex| SignalNames[OtherSignalIndex].as_str())
                            .collect::<Vec<_>>();
                        (
                            CandidateIndex,
                            Candidate.CandidateId.as_str(),
                            Candidate.MaterialCost,
                            Candidate.FootprintGrowth,
                            BlockingVariables,
                        )
                    })
                    .collect::<Vec<_>>(),
            );
        }
    }
    if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE").as_deref() == Ok("1") {
        eprintln!(
            "[debug] indexed assignment initial_ac_ms={}",
            InitialArcConsistencyStartedAt.elapsed().as_millis(),
        );
    }
    let RecursiveSearchStartedAt = std::time::Instant::now();
    let mut Success = false;
    // The factorized catalog's exact bounded DFS already uses the shared
    // expansion counter.  A min-conflict prepass consumed hundreds of those
    // expansions and several seconds without producing the RCA witness.
    let RunBoundedLocalSearch = PreferGuideFactorVariables
        && !SignalNames
            .iter()
            .any(|Signal| Signal.starts_with("__access_terminal__:"));
    let RunGreedySeedAndRepair = false;
    if PreferGuideFactorVariables {
        let GreedyStartedAt = std::time::Instant::now();
        let GreedySelection = BuildGreedyMaximalIndexedSelection(
            Groups,
            &SignalNames,
            &Compatibility,
            &IndexedDomains,
            Deadline,
            None,
        );
        let GreedySelectionCount = GreedySelection
            .iter()
            .filter(|Value| Value.is_some())
            .count();
        if GreedySelectionCount > BestSelection.iter().filter(|Value| Value.is_some()).count() {
            BestSelection = GreedySelection.clone();
        }
        if GreedySelectionCount == SignalNames.len() {
            let mut GreedyFailureNet = None;
            if SelectionHasPoweredAccessWitness(
                Groups,
                &SignalNames,
                &GreedySelection,
                Deadline,
                &mut GreedyFailureNet,
            ) == Some(true)
            {
                Selection = GreedySelection;
                Success = true;
            }
        }
        let mut GreedySeedCount = 0usize;
        let mut SeededSignalIndices = BTreeSet::new();
        while RunGreedySeedAndRepair && !Success && !*BudgetExhausted && !Deadline.Check() {
            let Some(SeedSignalIndex) = (0..SignalNames.len())
                .filter(|SignalIndex| {
                    BestSelection[*SignalIndex].is_none()
                        && !SeededSignalIndices.contains(SignalIndex)
                })
                .min_by_key(|SignalIndex| {
                    (
                        usize::from(!SignalNames[*SignalIndex].starts_with("__route_guide__:")),
                        DomainCount(&IndexedDomains[*SignalIndex]),
                        &SignalNames[*SignalIndex],
                    )
                })
            else {
                break;
            };
            SeededSignalIndices.insert(SeedSignalIndex);
            for CandidateIndex in 0..Compatibility[SeedSignalIndex].len() {
                if Deadline.Check()
                    || !DomainContains(&IndexedDomains[SeedSignalIndex], CandidateIndex)
                {
                    continue;
                }
                let SeedBudgetAvailable = SharedExpansionCount.is_none_or(|Shared| {
                    Shared
                        .fetch_update(AtomicOrdering::SeqCst, AtomicOrdering::SeqCst, |Value| {
                            (Value < MaximumExpansionCount).then_some(Value + 1)
                        })
                        .is_ok()
                });
                if !SeedBudgetAvailable {
                    *BudgetExhausted = true;
                    break;
                }
                GreedySeedCount += 1;
                let SeededSelection = BuildGreedyMaximalIndexedSelection(
                    Groups,
                    &SignalNames,
                    &Compatibility,
                    &IndexedDomains,
                    Deadline,
                    Some((SeedSignalIndex, CandidateIndex)),
                );
                let SeededSelectionCount = SeededSelection
                    .iter()
                    .filter(|Value| Value.is_some())
                    .count();
                if SeededSelectionCount
                    > BestSelection.iter().filter(|Value| Value.is_some()).count()
                {
                    BestSelection = SeededSelection.clone();
                }
                if SeededSelectionCount == SignalNames.len() {
                    let mut CandidateFailureNet = None;
                    if SelectionHasPoweredAccessWitness(
                        Groups,
                        &SignalNames,
                        &SeededSelection,
                        Deadline,
                        &mut CandidateFailureNet,
                    ) == Some(true)
                    {
                        Selection = SeededSelection;
                        Success = true;
                        break;
                    }
                }
            }
        }
        let mut GreedyRepairCount = 0usize;
        let mut RepairedSignalIndices = BTreeSet::new();
        while RunGreedySeedAndRepair && !Success && !*BudgetExhausted && !Deadline.Check() {
            let Some(PivotSignalIndex) = (0..SignalNames.len())
                .filter(|SignalIndex| {
                    BestSelection[*SignalIndex].is_none()
                        && !RepairedSignalIndices.contains(SignalIndex)
                })
                .min_by_key(|SignalIndex| {
                    (
                        usize::from(!SignalNames[*SignalIndex].starts_with("__route_guide__:")),
                        DomainCount(&IndexedDomains[*SignalIndex]),
                        &SignalNames[*SignalIndex],
                    )
                })
            else {
                break;
            };
            RepairedSignalIndices.insert(PivotSignalIndex);
            for PivotCandidateIndex in 0..Compatibility[PivotSignalIndex].len() {
                if Deadline.Check()
                    || !DomainContains(&IndexedDomains[PivotSignalIndex], PivotCandidateIndex)
                {
                    continue;
                }
                let RepairBudgetAvailable = SharedExpansionCount.is_none_or(|Shared| {
                    Shared
                        .fetch_update(AtomicOrdering::SeqCst, AtomicOrdering::SeqCst, |Value| {
                            (Value < MaximumExpansionCount).then_some(Value + 1)
                        })
                        .is_ok()
                });
                if !RepairBudgetAvailable {
                    *BudgetExhausted = true;
                    break;
                }
                GreedyRepairCount += 1;
                let RepairedSelection = BuildGreedyRepairIndexedSelection(
                    &SignalNames,
                    &Compatibility,
                    &IndexedDomains,
                    Deadline,
                    &BestSelection,
                    PivotSignalIndex,
                    PivotCandidateIndex,
                );
                let RepairedSelectionCount = RepairedSelection
                    .iter()
                    .filter(|Value| Value.is_some())
                    .count();
                if RepairedSelectionCount
                    > BestSelection.iter().filter(|Value| Value.is_some()).count()
                {
                    BestSelection = RepairedSelection.clone();
                    RepairedSignalIndices.clear();
                }
                if RepairedSelectionCount == SignalNames.len() {
                    let mut CandidateFailureNet = None;
                    if SelectionHasPoweredAccessWitness(
                        Groups,
                        &SignalNames,
                        &RepairedSelection,
                        Deadline,
                        &mut CandidateFailureNet,
                    ) == Some(true)
                    {
                        Selection = RepairedSelection;
                        Success = true;
                        break;
                    }
                }
            }
        }
        let mut MinConflictMoveCount = 0usize;
        let mut MinConflictBestConflictCount = usize::MAX;
        if RunBoundedLocalSearch && !Success && !*BudgetExhausted && !Deadline.Check() {
            if let Some(SharedExpansionCount) = SharedExpansionCount {
                let (MinConflictSelection, MoveCount, BestConflictCount, Exhausted) =
                    BuildBoundedMinConflictIndexedSelection(
                        &SignalNames,
                        &Compatibility,
                        &IndexedDomains,
                        &BestSelection,
                        SharedExpansionCount,
                        MaximumExpansionCount,
                        Deadline,
                        MaximumExpansionCount.min(512),
                    );
                MinConflictMoveCount = MoveCount;
                MinConflictBestConflictCount = BestConflictCount;
                *BudgetExhausted |= Exhausted;
                if let Some(MinConflictSelection) = MinConflictSelection {
                    BestSelection = MinConflictSelection.clone();
                    if BestConflictCount == 0 {
                        Selection = MinConflictSelection;
                        Success = true;
                    }
                }
            }
        }
        if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE").as_deref() == Ok("1") {
            let MissingVariables = BestSelection
                .iter()
                .enumerate()
                .filter_map(|(SignalIndex, CandidateIndex)| {
                    CandidateIndex
                        .is_none()
                        .then_some(&SignalNames[SignalIndex])
                })
                .collect::<Vec<_>>();
            eprintln!(
                "[debug] indexed assignment greedy_ms={} seeds={} repairs={} min_conflict_moves={} min_conflict_best={} selected={} complete={} missing={:?}",
                GreedyStartedAt.elapsed().as_millis(),
                GreedySeedCount,
                GreedyRepairCount,
                MinConflictMoveCount,
                MinConflictBestConflictCount,
                BestSelection.iter().filter(|Value| Value.is_some()).count(),
                Success,
                MissingVariables,
            );
        }
    }
    if !Success && PreferGuideFactorVariables && SharedExpansionCount.is_some() {
        let RootSignalIndex = (0..SignalNames.len())
            .filter(|SignalIndex| SignalNames[*SignalIndex].starts_with("__route_guide__:"))
            .min_by_key(|SignalIndex| {
                (
                    usize::from(!IsDetachedInternalGuideVariable(
                        Groups,
                        &SignalNames[*SignalIndex],
                    )),
                    DomainCount(&IndexedDomains[*SignalIndex]),
                    &SignalNames[*SignalIndex],
                )
            })
            .expect("factorized catalog contains guide variables");
        let mut RootCandidateIndices = (0..Groups[&SignalNames[RootSignalIndex]].len())
            .filter(|CandidateIndex| {
                DomainContains(&IndexedDomains[RootSignalIndex], *CandidateIndex)
            })
            .map(|CandidateIndex| {
                let CompatibleCounts = (0..SignalNames.len())
                    .filter(|OtherSignalIndex| *OtherSignalIndex != RootSignalIndex)
                    .map(|OtherSignalIndex| {
                        IntersectionCount(
                            &IndexedDomains[OtherSignalIndex],
                            &Compatibility[RootSignalIndex][CandidateIndex][OtherSignalIndex],
                        )
                    })
                    .collect::<Vec<_>>();
                (
                    CandidateIndex,
                    CompatibleCounts.iter().copied().min().unwrap_or(0),
                    CompatibleCounts.iter().sum::<usize>(),
                )
            })
            .collect::<Vec<_>>();
        RootCandidateIndices.sort_by_key(|(CandidateIndex, MinimumCompatible, TotalCompatible)| {
            (
                *CandidateIndex,
                std::cmp::Reverse(*MinimumCompatible),
                std::cmp::Reverse(*TotalCompatible),
            )
        });
        if let Some(PreferredCandidateIndex) = BestSelection[RootSignalIndex] {
            if let Some(PreferredPosition) =
                RootCandidateIndices
                    .iter()
                    .position(|(CandidateIndex, _Minimum, _Total)| {
                        *CandidateIndex == PreferredCandidateIndex
                    })
            {
                RootCandidateIndices[..=PreferredPosition].rotate_right(1);
            }
        }
        let SharedExpansionCount = SharedExpansionCount.unwrap();
        // Candidate order already includes the deterministic least-constraining
        // score. Evaluate a small bounded wave on the existing worker pool,
        // then consume outcomes in that exact order so a later witness cannot
        // outrank an earlier feasible branch.
        let WaveSize = RoutingThreadPool().current_num_threads().max(1);
        let RootCandidateCount = RootCandidateIndices.len();
        let RootBranchExpansionCount = MaximumExpansionCount.div_ceil(
            RootCandidateCount
                .min(RoutingThreadPool().current_num_threads().max(1))
                .max(1),
        );
        let mut AnyLocalBudgetExhausted = false;
        let mut RootCandidateOffset = 0usize;
        let mut WaveIndex = 0usize;
        'RootWaves: while RootCandidateOffset < RootCandidateIndices.len() {
            // Probe the highest-ranked root candidate by itself.  A complete
            // witness under that fixed candidate is already the earliest
            // deterministic root outcome, so launching later branches cannot
            // improve it.  If it fails, retain parallel waves for the rest of
            // the finite domain.
            let CurrentWaveSize = if RootCandidateOffset == 0 {
                1
            } else {
                WaveSize
            };
            let WaveEnd = (RootCandidateOffset + CurrentWaveSize).min(RootCandidateIndices.len());
            let Wave = &RootCandidateIndices[RootCandidateOffset..WaveEnd];
            let WaveStartedAt = std::time::Instant::now();
            let ExpansionCountBeforeWave = SharedExpansionCount.load(AtomicOrdering::SeqCst);
            let Outcomes = RoutingThreadPool().install(|| {
                Wave.par_iter()
                    .map(|(CandidateIndex, _Minimum, _Total)| {
                        SearchIndexedGuideBranchFromState(
                            Groups,
                            &SignalNames,
                            &Compatibility,
                            &IndexedDomains,
                            RootSignalIndex,
                            *CandidateIndex,
                            MaximumExpansionCount,
                            Deadline,
                            SharedExpansionCount,
                            EnforceFullArcConsistency,
                            PreferGuideFactorVariables,
                            &ConstraintNeighbors,
                            vec![false; SignalNames.len()],
                            vec![None; SignalNames.len()],
                            BestSelection.clone(),
                            0,
                            RootBranchExpansionCount,
                        )
                    })
                    .collect::<Vec<_>>()
            });
            if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE").as_deref() == Ok("1") {
                eprintln!(
                    "[debug] indexed root-wave index={} candidates={} elapsed_ms={} expansions={} successes={} incomplete={}",
                    WaveIndex,
                    Wave.len(),
                    WaveStartedAt.elapsed().as_millis(),
                    SharedExpansionCount
                        .load(AtomicOrdering::SeqCst)
                        .saturating_sub(ExpansionCountBeforeWave),
                    Outcomes.iter().filter(|Outcome| Outcome.Success).count(),
                    Outcomes.iter().filter(|Outcome| {
                        Outcome.BudgetExhausted || Outcome.DeadlineExceeded
                    }).count(),
                );
                eprintln!(
                    "[debug] indexed root-wave outcomes={:?}",
                    Outcomes
                        .iter()
                        .enumerate()
                        .map(|(Index, Outcome)| (
                            Index,
                            Outcome.ExpansionCount,
                            Outcome.Success,
                            Outcome.BudgetExhausted,
                            Outcome.DeadlineExceeded,
                            Outcome.FailureNet.clone(),
                        ))
                        .collect::<Vec<_>>(),
                );
            }
            for Outcome in Outcomes {
                if Outcome
                    .BestSelection
                    .iter()
                    .filter(|Value| Value.is_some())
                    .count()
                    > BestSelection.iter().filter(|Value| Value.is_some()).count()
                {
                    BestSelection = Outcome.BestSelection.clone();
                }
                if Outcome.FailureDepth > FailureDepth {
                    FailureDepth = Outcome.FailureDepth;
                    *FailureNet = Outcome.FailureNet.clone();
                    *ConflictSignals = Outcome.ConflictSignals.clone();
                    *ConflictResources = Outcome.ConflictResources.clone();
                }
                if Outcome.DeadlineExceeded {
                    break 'RootWaves;
                }
                if Outcome.BudgetExhausted {
                    if SharedExpansionCount.load(AtomicOrdering::SeqCst) >= MaximumExpansionCount {
                        *BudgetExhausted = true;
                        break 'RootWaves;
                    }
                    AnyLocalBudgetExhausted = true;
                }
                if Outcome.Success {
                    Selection = Outcome.Selection;
                    Success = true;
                    break 'RootWaves;
                }
            }
            RootCandidateOffset = WaveEnd;
            WaveIndex += 1;
        }
        if !Success && AnyLocalBudgetExhausted {
            *BudgetExhausted = true;
        }
        *ExpansionCount = SharedExpansionCount.load(AtomicOrdering::SeqCst);
    } else if !Success {
        Success = AssignIndexedCandidateDomains(
            Groups,
            &SignalNames,
            &Compatibility,
            &IndexedDomains,
            &mut Assigned,
            &mut Selection,
            &mut BestSelection,
            None,
            ExpansionCount,
            MaximumExpansionCount,
            BudgetExhausted,
            Deadline,
            &mut FailureDepth,
            FailureNet,
            ConflictSignals,
            ConflictResources,
            SharedExpansionCount,
            EnforceFullArcConsistency,
            PreferGuideFactorVariables,
            &ConstraintNeighbors,
            None,
        );
    }
    if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE").as_deref() == Ok("1") {
        eprintln!(
            "[debug] indexed assignment primary_search_ms={} recursive_ms={} success={}",
            StartedAt.elapsed().as_millis(),
            RecursiveSearchStartedAt.elapsed().as_millis(),
            Success,
        );
    }
    if !Success && !*BudgetExhausted && !Deadline.WasExceeded() {
        let mut GreedySelection = BuildGreedyMaximalIndexedSelection(
            Groups,
            &SignalNames,
            &Compatibility,
            &IndexedDomains,
            Deadline,
            None,
        );
        const MAXIMUM_GREEDY_SEEDS: usize = 2_048;
        let mut SeedCount = 0usize;
        'Signals: for SignalIndex in 0..SignalNames.len() {
            for CandidateIndex in 0..Compatibility[SignalIndex].len() {
                if !DomainContains(&IndexedDomains[SignalIndex], CandidateIndex) {
                    continue;
                }
                if SeedCount >= MAXIMUM_GREEDY_SEEDS || Deadline.Check() {
                    break 'Signals;
                }
                SeedCount += 1;
                let SeededSelection = BuildGreedyMaximalIndexedSelection(
                    Groups,
                    &SignalNames,
                    &Compatibility,
                    &IndexedDomains,
                    Deadline,
                    Some((SignalIndex, CandidateIndex)),
                );
                if SeededSelection
                    .iter()
                    .filter(|Value| Value.is_some())
                    .count()
                    > GreedySelection
                        .iter()
                        .filter(|Value| Value.is_some())
                        .count()
                {
                    GreedySelection = SeededSelection;
                }
            }
        }
        if GreedySelection
            .iter()
            .filter(|Value| Value.is_some())
            .count()
            > BestSelection.iter().filter(|Value| Value.is_some()).count()
        {
            BestSelection = GreedySelection;
        }
        if BestSelection.iter().filter(|Value| Value.is_some()).count() * 10
            >= SignalNames.len() * 7
        {
            const MAXIMUM_PARTIAL_EXPANSIONS: usize = 20_000;
            BestSelection = BuildMaximumPartialIndexedSelection(
                &SignalNames,
                &Compatibility,
                &IndexedDomains,
                BestSelection,
                MAXIMUM_PARTIAL_EXPANSIONS,
                Deadline,
            );
            if BestSelection.iter().filter(|Value| Value.is_some()).count() == SignalNames.len() {
                let mut CandidateFailureNet = None;
                if SelectionHasPoweredAccessWitness(
                    Groups,
                    &SignalNames,
                    &BestSelection,
                    Deadline,
                    &mut CandidateFailureNet,
                ) == Some(true)
                {
                    Selection = BestSelection.clone();
                    Success = true;
                }
            }
        }
    }
    let EffectiveSelection = if Success { &Selection } else { &BestSelection };
    Selected.clear();
    for (SignalIndex, CandidateIndex) in EffectiveSelection.iter().enumerate() {
        if let Some(CandidateIndex) = CandidateIndex {
            Selected.push((
                SignalNames[SignalIndex].clone(),
                Groups[&SignalNames[SignalIndex]][*CandidateIndex]
                    .CandidateId
                    .clone(),
            ));
        }
    }
    Some(Success)
}
