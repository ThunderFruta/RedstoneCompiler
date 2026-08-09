use crate::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Models::{AssignmentCandidate, ClaimMask};
use crate::RoutingThreadPool;
use rayon::prelude::*;
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};

type CandidateDomain = Vec<u64>;
type CandidateCompatibility = Vec<Vec<Vec<CandidateDomain>>>;

fn ParseContractRequirements(Encoded: &str) -> Vec<(&str, &str)> {
    Encoded
        .split(';')
        .filter_map(|Entry| {
            if Entry.is_empty() {
                None
            } else {
                Some(Entry.split_once('=').unwrap_or(("template", Entry)))
            }
        })
        .collect()
}

fn TemplatesAreCompatible(First: &AssignmentCandidate, Second: &AssignmentCandidate) -> bool {
    ParseContractRequirements(&First.TemplateKey)
        .iter()
        .all(|(FirstName, FirstValue)| {
            ParseContractRequirements(&Second.TemplateKey).iter().all(
                |(SecondName, SecondValue)| FirstName != SecondName || FirstValue == SecondValue,
            )
        })
}

fn DomainWordCount(CandidateCount: usize) -> usize {
    CandidateCount.div_ceil(64)
}

fn SetDomainBit(Domain: &mut CandidateDomain, CandidateIndex: usize) {
    Domain[CandidateIndex / 64] |= 1u64 << (CandidateIndex % 64);
}

fn ClearDomainBit(Domain: &mut CandidateDomain, CandidateIndex: usize) {
    Domain[CandidateIndex / 64] &= !(1u64 << (CandidateIndex % 64));
}

fn DomainContains(Domain: &CandidateDomain, CandidateIndex: usize) -> bool {
    Domain[CandidateIndex / 64] & (1u64 << (CandidateIndex % 64)) != 0
}

fn DomainCount(Domain: &CandidateDomain) -> usize {
    Domain.iter().map(|Value| Value.count_ones() as usize).sum()
}

fn DomainIsEmpty(Domain: &CandidateDomain) -> bool {
    Domain.iter().all(|Value| *Value == 0)
}

fn IntersectDomain(Domain: &mut CandidateDomain, Mask: &CandidateDomain) {
    for (Value, Allowed) in Domain.iter_mut().zip(Mask) {
        *Value &= *Allowed;
    }
}

fn IntersectionCount(First: &CandidateDomain, Second: &CandidateDomain) -> usize {
    First
        .iter()
        .zip(Second)
        .map(|(FirstWord, SecondWord)| (FirstWord & SecondWord).count_ones() as usize)
        .sum()
}

fn EnforceArcConsistency(
    SignalNames: &[String],
    Compatibility: &CandidateCompatibility,
    Domains: &mut [CandidateDomain],
    Assigned: &[bool],
    Deadline: &RuntimeDeadline,
    FailureNet: &mut Option<String>,
) -> bool {
    let mut PropagationSteps = 0usize;
    loop {
        let mut Changed = false;
        for FirstSignalIndex in 0..SignalNames.len() {
            if Assigned[FirstSignalIndex] {
                continue;
            }
            let CandidateCount = Compatibility[FirstSignalIndex].len();
            for FirstCandidateIndex in 0..CandidateCount {
                if !DomainContains(&Domains[FirstSignalIndex], FirstCandidateIndex) {
                    continue;
                }
                for SecondSignalIndex in 0..SignalNames.len() {
                    if Assigned[SecondSignalIndex] || SecondSignalIndex == FirstSignalIndex {
                        continue;
                    }
                    PropagationSteps += 1;
                    if PropagationSteps % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                        *FailureNet = Some(SignalNames[FirstSignalIndex].clone());
                        return false;
                    }
                    if IntersectionCount(
                        &Domains[SecondSignalIndex],
                        &Compatibility[FirstSignalIndex][FirstCandidateIndex][SecondSignalIndex],
                    ) == 0
                    {
                        ClearDomainBit(&mut Domains[FirstSignalIndex], FirstCandidateIndex);
                        Changed = true;
                        break;
                    }
                }
            }
            if DomainIsEmpty(&Domains[FirstSignalIndex]) {
                *FailureNet = Some(SignalNames[FirstSignalIndex].clone());
                return false;
            }
        }
        if !Changed {
            return !Deadline.Check();
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn RecordIndexedDeadEnd(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    SignalNames: &[String],
    Domains: &[CandidateDomain],
    Selection: &[Option<usize>],
    CurrentSignalIndex: usize,
    CurrentCandidateIndex: usize,
    FailingSignalIndex: usize,
    FailureDepth: &mut usize,
    FailureNet: &mut Option<String>,
    ConflictSignals: &mut Vec<String>,
    ConflictResources: &mut Vec<usize>,
    Deadline: &RuntimeDeadline,
) {
    let Depth = Selection.iter().filter(|Value| Value.is_some()).count() + 1;
    if Depth <= *FailureDepth || Deadline.Check() {
        return;
    }
    *FailureDepth = Depth;
    *FailureNet = Some(SignalNames[FailingSignalIndex].clone());
    ConflictSignals.clear();
    for (SignalIndex, CandidateIndex) in Selection.iter().enumerate() {
        if CandidateIndex.is_some() {
            ConflictSignals.push(SignalNames[SignalIndex].clone());
        }
    }
    ConflictSignals.push(SignalNames[CurrentSignalIndex].clone());
    ConflictSignals.push(SignalNames[FailingSignalIndex].clone());
    ConflictSignals.sort();
    ConflictSignals.dedup();

    ConflictResources.clear();
    let Candidate = &Groups[&SignalNames[CurrentSignalIndex]][CurrentCandidateIndex];
    for OtherCandidateIndex in 0..Groups[&SignalNames[FailingSignalIndex]].len() {
        if !DomainContains(&Domains[FailingSignalIndex], OtherCandidateIndex) {
            continue;
        }
        let Some(Indices) = Candidate.Claims.ConflictIndicesWithDeadline(
            &Groups[&SignalNames[FailingSignalIndex]][OtherCandidateIndex].Claims,
            Deadline,
        ) else {
            return;
        };
        ConflictResources.extend(Indices);
    }
    ConflictResources.sort_unstable();
    ConflictResources.dedup();
}

#[allow(clippy::too_many_arguments)]
fn AssignIndexedCandidateDomains(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    SignalNames: &[String],
    Compatibility: &CandidateCompatibility,
    Domains: &[CandidateDomain],
    Assigned: &mut [bool],
    Selection: &mut [Option<usize>],
    BestSelection: &mut Vec<Option<usize>>,
    ExpansionCount: &mut usize,
    MaximumExpansionCount: usize,
    BudgetExhausted: &mut bool,
    Deadline: &RuntimeDeadline,
    FailureDepth: &mut usize,
    FailureNet: &mut Option<String>,
    ConflictSignals: &mut Vec<String>,
    ConflictResources: &mut Vec<usize>,
) -> bool {
    if *BudgetExhausted || Deadline.Check() {
        return false;
    }
    let SelectionDepth = Selection.iter().filter(|Value| Value.is_some()).count();
    if SelectionDepth > BestSelection.iter().filter(|Value| Value.is_some()).count() {
        *BestSelection = Selection.to_vec();
    }
    if SelectionDepth == SignalNames.len() {
        return true;
    }

    let mut SelectedSignal = None;
    for SignalIndex in 0..SignalNames.len() {
        if Assigned[SignalIndex] {
            continue;
        }
        let Count = DomainCount(&Domains[SignalIndex]);
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
        .filter(|CandidateIndex| DomainContains(&Domains[SignalIndex], *CandidateIndex))
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
        if Deadline.Check() {
            *FailureNet = Some(SignalNames[SignalIndex].clone());
            return false;
        }
        *ExpansionCount += 1;
        if *ExpansionCount > MaximumExpansionCount {
            *BudgetExhausted = true;
            *FailureNet = Some(SignalNames[SignalIndex].clone());
            return false;
        }

        let mut NextDomains = Domains.to_vec();
        let mut Consistent = true;
        for OtherSignalIndex in 0..SignalNames.len() {
            if Assigned[OtherSignalIndex] || OtherSignalIndex == SignalIndex {
                continue;
            }
            IntersectDomain(
                &mut NextDomains[OtherSignalIndex],
                &Compatibility[SignalIndex][CandidateIndex][OtherSignalIndex],
            );
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
        let ShouldPropagate = Selection.iter().filter(|Value| Value.is_some()).count() >= 2;
        if ShouldPropagate
            && !EnforceArcConsistency(
                SignalNames,
                Compatibility,
                &mut NextDomains,
                Assigned,
                Deadline,
                FailureNet,
            )
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
            ExpansionCount,
            MaximumExpansionCount,
            BudgetExhausted,
            Deadline,
            FailureDepth,
            FailureNet,
            ConflictSignals,
            ConflictResources,
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

fn BuildGreedyMaximalIndexedSelection(
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
            .min_by_key(|Index| (DomainCount(&RemainingDomains[*Index]), &SignalNames[*Index]))
        else {
            break;
        };
        Completed[SignalIndex] = true;
        if DomainIsEmpty(&RemainingDomains[SignalIndex]) {
            continue;
        }
        let CandidateCount = Compatibility[SignalIndex].len();
        let SelectedCandidateIndex = (0..CandidateCount)
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

#[allow(clippy::too_many_arguments)]
fn SearchMaximumPartialIndexedSelection(
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

fn BuildMaximumPartialIndexedSelection(
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

fn TryAssignIndexedCandidates(
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
) -> Option<bool> {
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
    let mut Compatibility = SignalNames
        .iter()
        .map(|Signal| {
            (0..Groups[Signal].len())
                .map(|_| {
                    DomainWordCounts
                        .iter()
                        .map(|WordCount| vec![0u64; *WordCount])
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
                        let First = &Groups[&SignalNames[FirstSignalIndex]][*FirstCandidateIndex];
                        let Second =
                            &Groups[&SignalNames[SecondSignalIndex]][*SecondCandidateIndex];
                        if TemplatesAreCompatible(First, Second)
                            && (First.OwnerSignal == Second.OwnerSignal
                                || !First.Claims.Conflicts(&Second.Claims))
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
    PairwiseIncompatibleSignals.clear();
    for (FirstSignalIndex, SecondSignalIndex, CompatiblePairs) in PairCompatibility {
        let CompatiblePairs =
            CompatiblePairs.expect("deadline-free compatibility partition must be complete");
        if CompatiblePairs.is_empty() {
            PairwiseIncompatibleSignals.push((
                SignalNames[FirstSignalIndex].clone(),
                SignalNames[SecondSignalIndex].clone(),
            ));
        }
        for (FirstCandidateIndex, SecondCandidateIndex) in CompatiblePairs {
            SetDomainBit(
                &mut Compatibility[FirstSignalIndex][FirstCandidateIndex][SecondSignalIndex],
                SecondCandidateIndex,
            );
            SetDomainBit(
                &mut Compatibility[SecondSignalIndex][SecondCandidateIndex][FirstSignalIndex],
                FirstCandidateIndex,
            );
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
    let mut Success = AssignIndexedCandidateDomains(
        Groups,
        &SignalNames,
        &Compatibility,
        &IndexedDomains,
        &mut Assigned,
        &mut Selection,
        &mut BestSelection,
        ExpansionCount,
        MaximumExpansionCount,
        BudgetExhausted,
        Deadline,
        &mut FailureDepth,
        FailureNet,
        ConflictSignals,
        ConflictResources,
    );
    if !Success && !*BudgetExhausted && !Deadline.WasExceeded() {
        let mut GreedySelection = BuildGreedyMaximalIndexedSelection(
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
                Selection = BestSelection.clone();
                Success = true;
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

pub(crate) fn CandidateOrder(Value: &AssignmentCandidate) -> (i32, i32, i32, i32, i32, &str) {
    (
        Value.MaterialCost,
        Value.FootprintGrowth,
        Value.Length,
        Value.BendCount,
        Value.ViaCount,
        &Value.CandidateId,
    )
}

pub(crate) fn SortCandidatesWithDeadline(
    Values: &mut Vec<AssignmentCandidate>,
    Deadline: &RuntimeDeadline,
) -> bool {
    const SORT_CHUNK_SIZE: usize = 256;
    if Deadline.Check() {
        return false;
    }
    let mut Indices = Vec::with_capacity(Values.len());
    for Index in 0..Values.len() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return false;
        }
        Indices.push(Index);
    }
    for Chunk in Indices.chunks_mut(SORT_CHUNK_SIZE) {
        if Deadline.Check() {
            return false;
        }
        Chunk.sort_by(|First, Second| {
            CandidateOrder(&Values[*First]).cmp(&CandidateOrder(&Values[*Second]))
        });
        if Deadline.Check() {
            return false;
        }
    }

    let mut Width = SORT_CHUNK_SIZE;
    while Width < Indices.len() {
        let mut Merged = Vec::with_capacity(Indices.len());
        for Start in (0..Indices.len()).step_by(Width.saturating_mul(2)) {
            if Deadline.Check() {
                return false;
            }
            let Middle = (Start + Width).min(Indices.len());
            let End = (Middle + Width).min(Indices.len());
            let mut Left = Start;
            let mut Right = Middle;
            let mut CompletedComparisons = 0usize;
            while Left < Middle && Right < End {
                if CompletedComparisons % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return false;
                }
                let OrderingValue = CandidateOrder(&Values[Indices[Left]])
                    .cmp(&CandidateOrder(&Values[Indices[Right]]));
                if OrderingValue != Ordering::Greater {
                    Merged.push(Indices[Left]);
                    Left += 1;
                } else {
                    Merged.push(Indices[Right]);
                    Right += 1;
                }
                CompletedComparisons += 1;
            }
            for Index in &Indices[Left..Middle] {
                if Merged.len() % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return false;
                }
                Merged.push(*Index);
            }
            for Index in &Indices[Right..End] {
                if Merged.len() % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return false;
                }
                Merged.push(*Index);
            }
        }
        Indices = Merged;
        Width = Width.saturating_mul(2);
    }
    if Deadline.Check() {
        return false;
    }
    let OriginalValues = std::mem::take(Values);
    let mut Original = Vec::with_capacity(OriginalValues.len());
    for (Index, Value) in OriginalValues.into_iter().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return false;
        }
        Original.push(Some(Value));
    }
    Values.reserve(Original.len());
    for (Index, OriginalIndex) in Indices.into_iter().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return false;
        }
        Values.push(Original[OriginalIndex].take().unwrap());
    }
    !Deadline.Check()
}

pub(crate) fn AssignCandidates(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    Remaining: &[String],
    Owned: &ClaimMask,
    BaseBySignal: &BTreeMap<String, ClaimMask>,
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
) -> bool {
    if Deadline.Check() {
        *FailureNet = Remaining.first().cloned();
        return false;
    }
    let mut Domains: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for Signal in Remaining {
        if Deadline.Check() {
            *FailureNet = Some(Signal.clone());
            return false;
        }
        let mut Compatible = Vec::new();
        for (Index, Candidate) in Groups[Signal].iter().enumerate() {
            if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                *FailureNet = Some(Signal.clone());
                return false;
            }
            match Candidate.Claims.ConflictsWithDeadline(Owned, Deadline) {
                Some(true) => continue,
                Some(false) => {}
                None => {
                    *FailureNet = Some(Signal.clone());
                    return false;
                }
            }
            let mut ConflictsWithBase = false;
            for (BaseSignal, BaseClaims) in BaseBySignal {
                if Deadline.Check() {
                    *FailureNet = Some(Signal.clone());
                    return false;
                }
                if BaseSignal != Signal {
                    match Candidate.Claims.ConflictsWithDeadline(BaseClaims, Deadline) {
                        Some(true) => {
                            ConflictsWithBase = true;
                            break;
                        }
                        Some(false) => {}
                        None => {
                            *FailureNet = Some(Signal.clone());
                            return false;
                        }
                    }
                }
            }
            if ConflictsWithBase {
                continue;
            }
            Compatible.push(Index);
        }
        Domains.insert(Signal.clone(), Compatible);
    }
    if let Some(Result) = TryAssignIndexedCandidates(
        Groups,
        &Domains,
        Selected,
        ExpansionCount,
        MaximumExpansionCount,
        BudgetExhausted,
        Deadline,
        FailureNet,
        ConflictSignals,
        ConflictResources,
        PairwiseIncompatibleSignals,
        PairwiseCompatibilityComplete,
    ) {
        return Result;
    }
    AssignCandidateDomains(
        Groups,
        &Domains,
        Selected,
        ExpansionCount,
        MaximumExpansionCount,
        BudgetExhausted,
        Deadline,
        FailureNet,
        ConflictSignals,
        ConflictResources,
    )
}

fn RecordConflictSignals(
    Selected: &[(String, String)],
    CurrentSignal: &str,
    FailingSignal: Option<&str>,
    ConflictSignals: &mut Vec<String>,
    Deadline: &RuntimeDeadline,
) -> bool {
    let mut Signals = BTreeSet::new();
    for (Index, (Signal, _CandidateId)) in Selected.iter().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return false;
        }
        Signals.insert(Signal.clone());
    }
    Signals.insert(CurrentSignal.to_string());
    if let Some(Signal) = FailingSignal {
        Signals.insert(Signal.to_string());
    }
    ConflictSignals.clear();
    for (Index, Signal) in Signals.into_iter().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return false;
        }
        ConflictSignals.push(Signal);
    }
    !Deadline.Check()
}

fn AssignCandidateDomains(
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
) -> bool {
    if *BudgetExhausted || Deadline.Check() {
        return false;
    }
    if Domains.is_empty() {
        return true;
    }
    let mut Signal = None;
    for (Index, (CandidateSignal, Values)) in Domains.iter().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return false;
        }
        let IsBetter = Signal
            .as_ref()
            .is_none_or(|(BestSignal, BestCount): &(String, usize)| {
                (Values.len(), CandidateSignal) < (*BestCount, BestSignal)
            });
        if IsBetter {
            Signal = Some((CandidateSignal.clone(), Values.len()));
        }
    }
    let Signal = Signal.unwrap().0;
    if Domains[&Signal].is_empty() {
        *FailureNet = Some(Signal.clone());
        if !RecordConflictSignals(Selected, &Signal, None, ConflictSignals, Deadline) {
            return false;
        }
        return false;
    }
    let SelectionDepth = Selected.len();
    let mut FailureSelection = Vec::new();
    for CandidateIndex in &Domains[&Signal] {
        if Deadline.Check() {
            *FailureNet = Some(Signal.clone());
            return false;
        }
        let Candidate = &Groups[&Signal][*CandidateIndex];
        *ExpansionCount += 1;
        if *ExpansionCount > MaximumExpansionCount {
            *BudgetExhausted = true;
            *FailureNet = Some(Signal.clone());
            return false;
        }
        let mut NextDomains = BTreeMap::new();
        let mut Consistent = true;
        for (OtherSignal, OtherDomain) in Domains {
            if Deadline.Check() {
                *FailureNet = Some(OtherSignal.clone());
                return false;
            }
            if OtherSignal == &Signal {
                continue;
            }
            let mut Compatible = Vec::new();
            for (Index, OtherIndex) in OtherDomain.iter().copied().enumerate() {
                if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    *FailureNet = Some(OtherSignal.clone());
                    return false;
                }
                let Other = &Groups[OtherSignal][OtherIndex];
                if !TemplatesAreCompatible(Candidate, Other) {
                    continue;
                }
                if Candidate.OwnerSignal == Other.OwnerSignal {
                    Compatible.push(OtherIndex);
                    continue;
                }
                match Candidate
                    .Claims
                    .ConflictsWithDeadline(&Other.Claims, Deadline)
                {
                    Some(false) => Compatible.push(OtherIndex),
                    Some(true) => {}
                    None => {
                        *FailureNet = Some(OtherSignal.clone());
                        return false;
                    }
                }
            }
            if Compatible.is_empty() {
                *FailureNet = Some(OtherSignal.clone());
                if !RecordConflictSignals(
                    Selected,
                    &Signal,
                    Some(OtherSignal),
                    ConflictSignals,
                    Deadline,
                ) {
                    *FailureNet = Some(OtherSignal.clone());
                    return false;
                }
                FailureSelection = Selected.clone();
                FailureSelection.push((Signal.clone(), Candidate.CandidateId.clone()));
                for OtherIndex in OtherDomain {
                    if Deadline.Check() {
                        *FailureNet = Some(OtherSignal.clone());
                        return false;
                    }
                    let Some(Indices) = Candidate.Claims.ConflictIndicesWithDeadline(
                        &Groups[OtherSignal][*OtherIndex].Claims,
                        Deadline,
                    ) else {
                        *FailureNet = Some(OtherSignal.clone());
                        return false;
                    };
                    for (Index, Resource) in Indices.into_iter().enumerate() {
                        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                            *FailureNet = Some(OtherSignal.clone());
                            return false;
                        }
                        ConflictResources.push(Resource);
                    }
                }
                if Deadline.Check() {
                    *FailureNet = Some(OtherSignal.clone());
                    return false;
                }
                let mut UniqueResources = BTreeSet::new();
                for (Index, Resource) in ConflictResources.drain(..).enumerate() {
                    if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                        *FailureNet = Some(OtherSignal.clone());
                        return false;
                    }
                    UniqueResources.insert(Resource);
                }
                for (Index, Resource) in UniqueResources.into_iter().enumerate() {
                    if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                        *FailureNet = Some(OtherSignal.clone());
                        return false;
                    }
                    ConflictResources.push(Resource);
                }
                Consistent = false;
                break;
            }
            NextDomains.insert(OtherSignal.clone(), Compatible);
        }
        if !Consistent {
            continue;
        }
        Selected.push((Signal.clone(), Candidate.CandidateId.clone()));
        if AssignCandidateDomains(
            Groups,
            &NextDomains,
            Selected,
            ExpansionCount,
            MaximumExpansionCount,
            BudgetExhausted,
            Deadline,
            FailureNet,
            ConflictSignals,
            ConflictResources,
        ) {
            return true;
        }
        let RecursiveFailureSelection = Selected.clone();
        Selected.truncate(SelectionDepth);
        if RecursiveFailureSelection.len() > FailureSelection.len() {
            FailureSelection = RecursiveFailureSelection;
        }
        if *BudgetExhausted || Deadline.WasExceeded() {
            if !FailureSelection.is_empty() {
                *Selected = FailureSelection;
            }
            return false;
        }
    }
    if !FailureSelection.is_empty() {
        *Selected = FailureSelection;
    }
    false
}

#[cfg(test)]
mod Tests {
    use super::*;

    fn Candidate(CandidateId: &str, MaterialCost: i32) -> AssignmentCandidate {
        AssignmentCandidate {
            CandidateId: CandidateId.to_string(),
            OwnerSignal: "Signal".to_string(),
            TemplateKey: String::new(),
            Claims: std::sync::Arc::new(ClaimMask::default()),
            MaterialCost,
            FootprintGrowth: 1,
            Length: 1,
            BendCount: 0,
            ViaCount: 0,
        }
    }

    #[test]
    fn NamedContractRequirementsPermitFactoredDimensions() {
        let CoreA = AssignmentCandidate {
            CandidateId: "core-a".to_string(),
            OwnerSignal: "Core".to_string(),
            TemplateKey: "core=a".to_string(),
            Claims: std::sync::Arc::new(ClaimMask::default()),
            MaterialCost: 0,
            FootprintGrowth: 0,
            Length: 0,
            BendCount: 0,
            ViaCount: 0,
        };
        let InterfaceNorth = AssignmentCandidate {
            CandidateId: "interface-north".to_string(),
            TemplateKey: "interface=north".to_string(),
            ..CoreA.clone()
        };
        let CoreB = AssignmentCandidate {
            CandidateId: "core-b".to_string(),
            TemplateKey: "core=b".to_string(),
            ..CoreA.clone()
        };

        assert!(TemplatesAreCompatible(&CoreA, &InterfaceNorth));
        assert!(!TemplatesAreCompatible(&CoreA, &CoreB));
    }

    #[test]
    fn DeadlineAwareCandidateSortPreservesDeterministicOrder() {
        let mut Values = vec![
            Candidate("later", 2),
            Candidate("zeta", 1),
            Candidate("alpha", 1),
        ];
        let Deadline = RuntimeDeadline::FromMilliseconds(Some(1_000)).unwrap();

        assert!(SortCandidatesWithDeadline(&mut Values, &Deadline));
        assert_eq!(
            Values
                .iter()
                .map(|Value| Value.CandidateId.as_str())
                .collect::<Vec<_>>(),
            vec!["alpha", "zeta", "later"],
        );
    }

    #[test]
    fn DeadlineAwareCandidateSortStopsOnExpiredDeadline() {
        let mut Values = vec![Candidate("candidate", 1)];
        let Deadline = RuntimeDeadline::FromMilliseconds(Some(0)).unwrap();

        assert!(!SortCandidatesWithDeadline(&mut Values, &Deadline));
        assert!(Deadline.WasExceeded());
    }

    #[test]
    fn GreedyMaximalSelectionKeepsCompatibleSubset() {
        let SignalNames = vec!["A".to_string(), "B".to_string(), "C".to_string()];
        let mut Compatibility = (0..3)
            .map(|_| vec![(0..3).map(|_| vec![0u64; 1]).collect::<Vec<_>>()])
            .collect::<CandidateCompatibility>();
        for SignalIndex in 0..3 {
            SetDomainBit(&mut Compatibility[SignalIndex][0][SignalIndex], 0);
        }
        SetDomainBit(&mut Compatibility[0][0][1], 0);
        SetDomainBit(&mut Compatibility[1][0][0], 0);
        SetDomainBit(&mut Compatibility[0][0][2], 0);
        SetDomainBit(&mut Compatibility[2][0][0], 0);
        let Domains = vec![vec![1u64], vec![1u64], vec![1u64]];
        let Deadline = RuntimeDeadline::FromMilliseconds(Some(1_000)).unwrap();

        let Selected = BuildGreedyMaximalIndexedSelection(
            &SignalNames,
            &Compatibility,
            &Domains,
            &Deadline,
            None,
        );

        assert_eq!(Selected.iter().filter(|Value| Value.is_some()).count(), 2,);
        assert!(Selected[0].is_some());

        let MaximumSelected = BuildMaximumPartialIndexedSelection(
            &SignalNames,
            &Compatibility,
            &Domains,
            Selected,
            1_000,
            &Deadline,
        );
        assert_eq!(
            MaximumSelected
                .iter()
                .filter(|Value| Value.is_some())
                .count(),
            2,
        );
    }
}
