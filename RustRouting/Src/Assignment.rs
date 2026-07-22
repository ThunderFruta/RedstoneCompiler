use crate::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Models::{AssignmentCandidate, ClaimMask};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};

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
                match Candidate
                    .Claims
                    .ConflictsWithDeadline(&Groups[OtherSignal][OtherIndex].Claims, Deadline)
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
        Selected.pop();
        if *BudgetExhausted || Deadline.WasExceeded() {
            return false;
        }
    }
    false
}

#[cfg(test)]
mod Tests {
    use super::*;

    fn Candidate(CandidateId: &str, MaterialCost: i32) -> AssignmentCandidate {
        AssignmentCandidate {
            CandidateId: CandidateId.to_string(),
            Claims: ClaimMask::default(),
            MaterialCost,
            FootprintGrowth: 1,
            Length: 1,
            BendCount: 0,
            ViaCount: 0,
        }
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
}
