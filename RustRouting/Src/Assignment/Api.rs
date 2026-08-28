//! Stable crate-facing assignment API.

use crate::Core::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Core::Models::{AssignmentCandidate, ClaimMask};
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::atomic::{AtomicUsize, Ordering as AtomicOrdering};
use std::sync::Arc;

#[cfg(test)]
use super::Domains::*;
use super::Search::*;
use super::Witness::*;

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
    UsePairwiseCompatibilityIndex: bool,
    FailureNet: &mut Option<String>,
    ConflictSignals: &mut Vec<String>,
    ConflictResources: &mut Vec<usize>,
    PairwiseIncompatibleSignals: &mut Vec<(String, String)>,
    PairwiseCompatibilityComplete: &mut bool,
    CollectConflictResources: bool,
    SharedExpansionCount: Option<&AtomicUsize>,
    CrossAirByWire: Option<&[Vec<(usize, usize)>]>,
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
    if UsePairwiseCompatibilityIndex {
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
            SharedExpansionCount,
            CrossAirByWire,
        ) {
            return Result;
        }
    }
    let mut PhysicalConflictCache = HashMap::new();
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
        &mut PhysicalConflictCache,
        CollectConflictResources,
        SharedExpansionCount,
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
    PhysicalConflictCache: &mut HashMap<(usize, usize), bool>,
    CollectConflictResources: bool,
    SharedExpansionCount: Option<&AtomicUsize>,
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
        let DeferredSyntheticOwnershipChoice = Groups[CandidateSignal]
            .iter()
            .all(|Candidate| Candidate.OwnerSignal != *CandidateSignal);
        let IsBetter = Signal.as_ref().is_none_or(
            |(BestSignal, BestDeferredSyntheticOwnershipChoice, BestCount): &(
                String,
                bool,
                usize,
            )| {
                (
                    DeferredSyntheticOwnershipChoice,
                    Values.len(),
                    CandidateSignal,
                ) < (
                    *BestDeferredSyntheticOwnershipChoice,
                    *BestCount,
                    BestSignal,
                )
            },
        );
        if IsBetter {
            Signal = Some((
                CandidateSignal.clone(),
                DeferredSyntheticOwnershipChoice,
                Values.len(),
            ));
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
                    match Candidate
                        .Claims
                        .SameOwnerConflictsWithDeadline(&Other.Claims, Deadline)
                    {
                        Some(false) => Compatible.push(OtherIndex),
                        Some(true) => {}
                        None => {
                            *FailureNet = Some(OtherSignal.clone());
                            return false;
                        }
                    }
                    continue;
                }
                let CandidateMaskIdentity = Arc::as_ptr(&Candidate.Claims) as usize;
                let OtherMaskIdentity = Arc::as_ptr(&Other.Claims) as usize;
                let PhysicalIdentity = if CandidateMaskIdentity <= OtherMaskIdentity {
                    (CandidateMaskIdentity, OtherMaskIdentity)
                } else {
                    (OtherMaskIdentity, CandidateMaskIdentity)
                };
                let Conflict = if let Some(Cached) = PhysicalConflictCache.get(&PhysicalIdentity) {
                    Some(*Cached)
                } else {
                    let Result = Candidate
                        .Claims
                        .ConflictsWithDeadline(&Other.Claims, Deadline);
                    if let Some(Value) = Result {
                        PhysicalConflictCache.insert(PhysicalIdentity, Value);
                    }
                    Result
                };
                match Conflict {
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
                if CollectConflictResources {
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
            PhysicalConflictCache,
            CollectConflictResources,
            SharedExpansionCount,
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
            TemplateRequirements: ParseContractRequirements(""),
            ForbiddenCandidateIds: std::sync::Arc::new(Vec::new()),
            OrderedWire: std::sync::Arc::new(Vec::new()),
            PoweredAccessConstraint: None,
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
            TemplateRequirements: ParseContractRequirements("core=a"),
            ForbiddenCandidateIds: std::sync::Arc::new(Vec::new()),
            OrderedWire: std::sync::Arc::new(Vec::new()),
            PoweredAccessConstraint: None,
            Claims: std::sync::Arc::new(ClaimMask::default()),
            MaterialCost: 0,
            FootprintGrowth: 0,
            Length: 0,
            BendCount: 0,
            ViaCount: 0,
        };
        let InterfaceNorth = AssignmentCandidate {
            CandidateId: "interface-north".to_string(),
            TemplateRequirements: ParseContractRequirements("interface=north"),
            ..CoreA.clone()
        };
        let CoreB = AssignmentCandidate {
            CandidateId: "core-b".to_string(),
            TemplateRequirements: ParseContractRequirements("core=b"),
            ..CoreA.clone()
        };

        assert!(TemplatesAreCompatible(&CoreA, &InterfaceNorth));
        assert!(!TemplatesAreCompatible(&CoreA, &CoreB));
    }

    #[test]
    fn SparseCompatibilityRejectsEmergentSameOwnerCrossAirSupportConflict() {
        let BuildAccess = |LogicalKey: &str,
                           CandidateId: &str,
                           Wire: &[usize],
                           Support: &[usize]| {
            AssignmentCandidate {
            CandidateId: CandidateId.to_string(),
            OwnerSignal: "Net".to_string(),
            TemplateRequirements: ParseContractRequirements(&format!(
                "access-stub:{LogicalKey}={CandidateId};access-portal:{LogicalKey}=0,0,0;access-layer:Net=1"
            )),
            ForbiddenCandidateIds: Arc::new(Vec::new()),
            OrderedWire: Arc::new(Vec::new()),
            PoweredAccessConstraint: None,
            Claims: Arc::new(
                ClaimMask::FromIndices(3, Wire, Support, &[], &[])
                    .expect("test claims are in range"),
            ),
            MaterialCost: 0,
            FootprintGrowth: 0,
            Length: 1,
            BendCount: 0,
            ViaCount: 0,
        }
        };
        let FirstVariable = "__access_terminal__:A".to_string();
        let SecondVariable = "__access_terminal__:B".to_string();
        let Groups = BTreeMap::from([
            (
                FirstVariable.clone(),
                vec![BuildAccess("A", "a", &[0], &[2])],
            ),
            (
                SecondVariable.clone(),
                vec![BuildAccess("B", "b", &[1], &[])],
            ),
        ]);
        let SignalNames = vec![FirstVariable.clone(), SecondVariable.clone()];
        let Domains = BTreeMap::from([(FirstVariable, vec![0]), (SecondVariable, vec![0])]);
        let IndexedDomains = vec![vec![1], vec![1]];
        let CrossAirByWire = vec![vec![(1, 2)], Vec::new(), Vec::new()];
        let Deadline = RuntimeDeadline::FromMilliseconds(Some(1_000)).unwrap();

        let Compatibility = BuildSparseAccessCompatibility(
            &Groups,
            &SignalNames,
            &Domains,
            &IndexedDomains,
            Some(&CrossAirByWire),
            &Deadline,
        )
        .expect("factorized access domain uses sparse compatibility");

        assert!(!DomainContains(&Compatibility[0][0][1], 0));
        assert!(!DomainContains(&Compatibility[1][0][0], 0));
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
            .map(|_| vec![(0..3).map(|_| Arc::new(vec![0u64; 1])).collect::<Vec<_>>()])
            .collect::<CandidateCompatibility>();
        for SignalIndex in 0..3 {
            SetDomainBit(
                Arc::make_mut(&mut Compatibility[SignalIndex][0][SignalIndex]),
                0,
            );
        }
        SetDomainBit(Arc::make_mut(&mut Compatibility[0][0][1]), 0);
        SetDomainBit(Arc::make_mut(&mut Compatibility[1][0][0]), 0);
        SetDomainBit(Arc::make_mut(&mut Compatibility[0][0][2]), 0);
        SetDomainBit(Arc::make_mut(&mut Compatibility[2][0][0]), 0);
        let Domains = vec![vec![1u64], vec![1u64], vec![1u64]];
        let Deadline = RuntimeDeadline::FromMilliseconds(Some(1_000)).unwrap();

        let Selected = BuildGreedyMaximalIndexedSelection(
            &BTreeMap::new(),
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
