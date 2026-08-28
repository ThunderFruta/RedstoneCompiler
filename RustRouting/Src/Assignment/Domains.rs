//! Sparse candidate domains and arc-consistency propagation.

use crate::Core::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Core::Models::AssignmentCandidate;
use crate::Core::Runtime::RoutingThreadPool;
use rayon::prelude::*;
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::sync::Arc;

use super::Witness::*;

pub(super) fn IntersectDomain(Domain: &mut CandidateDomain, Mask: &CandidateDomain) {
    for (Value, Allowed) in Domain.iter_mut().zip(Mask) {
        *Value &= *Allowed;
    }
}

pub(super) fn IntersectionCount(First: &CandidateDomain, Second: &CandidateDomain) -> usize {
    First
        .iter()
        .zip(Second)
        .map(|(FirstWord, SecondWord)| (FirstWord & SecondWord).count_ones() as usize)
        .sum()
}

pub(super) fn AccessContractIsLocalToVariable(
    Signal: &str,
    Candidate: &AssignmentCandidate,
) -> bool {
    let Some(LogicalKey) = Signal.strip_prefix("__access_terminal__:") else {
        return false;
    };
    let LocalStubRequirement = format!("access-stub:{LogicalKey}");
    let SharedPortalRequirement = format!("access-portal:{LogicalKey}");
    let OwnerLayerRequirement = format!("access-layer:{}", Candidate.OwnerSignal);
    Candidate
        .TemplateRequirements
        .iter()
        .filter(|(Name, _Value)| *Name == LocalStubRequirement)
        .count()
        == 1
        && Candidate.TemplateRequirements.iter().all(|(Name, _Value)| {
            *Name == LocalStubRequirement
                || *Name == SharedPortalRequirement
                || *Name == OwnerLayerRequirement
        })
}

pub(super) fn ClearSparseAccessConflict(
    Compatibility: &mut CandidateCompatibility,
    First: (usize, usize),
    Second: (usize, usize),
) {
    if First.0 == Second.0 || First == Second {
        return;
    }
    // Every caller has already proved this exact pair incompatible.  In
    // particular, CrossAirByWire can prove an air/support contradiction that
    // emerges only after two same-owner wire sets are united; rechecking the
    // candidates' individual masks here would erase that valid no-good.
    ClearDomainBit(
        Arc::make_mut(&mut Compatibility[First.0][First.1][Second.0]),
        Second.1,
    );
    ClearDomainBit(
        Arc::make_mut(&mut Compatibility[Second.0][Second.1][First.0]),
        First.1,
    );
}

pub(super) fn BuildSparseAccessCompatibility(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    SignalNames: &[String],
    Domains: &BTreeMap<String, Vec<usize>>,
    IndexedDomains: &[CandidateDomain],
    CrossAirByWire: Option<&[Vec<(usize, usize)>]>,
    Deadline: &RuntimeDeadline,
) -> Option<CandidateCompatibility> {
    let StartedAt = std::time::Instant::now();
    let SparseFactorDomain = SignalNames
        .iter()
        .any(|Signal| Signal.starts_with("__access_terminal__:"))
        && SignalNames.iter().all(|Signal| {
            if Signal.starts_with("__access_terminal__:") {
                return Domains[Signal].iter().all(|CandidateIndex| {
                    AccessContractIsLocalToVariable(Signal, &Groups[Signal][*CandidateIndex])
                });
            }
            Signal.starts_with("__route_guide__:") || Signal.starts_with("__base_claim__:")
        });
    if !SparseFactorDomain {
        return None;
    }
    let SharedIndexedDomains = IndexedDomains
        .iter()
        .cloned()
        .map(Arc::new)
        .collect::<Vec<_>>();
    let mut Compatibility = SignalNames
        .iter()
        .map(|Signal| {
            (0..Groups[Signal].len())
                .map(|_| SharedIndexedDomains.clone())
                .collect::<Vec<_>>()
        })
        .collect::<CandidateCompatibility>();
    if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE").as_deref() == Ok("1") {
        eprintln!(
            "[debug] sparse compatibility initialized_ms={}",
            StartedAt.elapsed().as_millis(),
        );
    }
    let mut ChoicesBySharedRequirement =
        HashMap::<String, BTreeMap<String, Vec<(usize, usize)>>>::new();
    for (SignalIndex, Signal) in SignalNames.iter().enumerate() {
        for CandidateIndex in &Domains[Signal] {
            let Candidate = &Groups[Signal][*CandidateIndex];
            for (Name, Value) in Candidate.TemplateRequirements.iter() {
                ChoicesBySharedRequirement
                    .entry(Name.clone())
                    .or_default()
                    .entry(Value.clone())
                    .or_default()
                    .push((SignalIndex, *CandidateIndex));
            }
        }
    }
    let mut CompletedContractPairs = 0usize;
    for ValuesByChoice in ChoicesBySharedRequirement.values() {
        let Choices = ValuesByChoice.values().collect::<Vec<_>>();
        for FirstChoiceIndex in 0..Choices.len() {
            for SecondChoice in Choices.iter().skip(FirstChoiceIndex + 1) {
                for First in Choices[FirstChoiceIndex] {
                    for Second in *SecondChoice {
                        if CompletedContractPairs % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check()
                        {
                            return None;
                        }
                        if First.0 != Second.0 {
                            ClearDomainBit(
                                Arc::make_mut(&mut Compatibility[First.0][First.1][Second.0]),
                                Second.1,
                            );
                            ClearDomainBit(
                                Arc::make_mut(&mut Compatibility[Second.0][Second.1][First.0]),
                                First.1,
                            );
                        }
                        CompletedContractPairs += 1;
                    }
                }
            }
        }
    }
    if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE").as_deref() == Ok("1") {
        eprintln!(
            "[debug] sparse compatibility contracts_ms={} pairs={}",
            StartedAt.elapsed().as_millis(),
            CompletedContractPairs,
        );
    }
    let CandidateEndpointByChoice = SignalNames
        .iter()
        .enumerate()
        .flat_map(|(SignalIndex, Signal)| {
            Domains[Signal].iter().map(move |CandidateIndex| {
                (
                    (
                        Signal.clone(),
                        Groups[Signal][*CandidateIndex].CandidateId.clone(),
                    ),
                    (SignalIndex, *CandidateIndex),
                )
            })
        })
        .collect::<HashMap<_, _>>();
    let mut ExplicitConflictPairs = Vec::new();
    for (SignalIndex, Signal) in SignalNames.iter().enumerate() {
        for CandidateIndex in &Domains[Signal] {
            let Candidate = &Groups[Signal][*CandidateIndex];
            for ForbiddenChoice in Candidate.ForbiddenCandidateIds.iter() {
                if let Some(ForbiddenEndpoint) =
                    CandidateEndpointByChoice.get(ForbiddenChoice).copied()
                {
                    ExplicitConflictPairs.push(((SignalIndex, *CandidateIndex), ForbiddenEndpoint));
                }
            }
        }
    }
    ExplicitConflictPairs.sort_unstable();
    ExplicitConflictPairs.dedup();
    for (Index, (First, Second)) in ExplicitConflictPairs.iter().copied().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Some(Compatibility);
        }
        ClearSparseAccessConflict(&mut Compatibility, First, Second);
    }
    if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE").as_deref() == Ok("1") {
        eprintln!(
            "[debug] sparse compatibility materializability_ms={} pairs={}",
            StartedAt.elapsed().as_millis(),
            ExplicitConflictPairs.len(),
        );
    }
    let mut Occupants: HashMap<usize, AccessResourceCandidates> = HashMap::new();
    let mut CompletedClaims = 0usize;
    for (SignalIndex, Signal) in SignalNames.iter().enumerate() {
        for CandidateIndex in &Domains[Signal] {
            let Candidate = &Groups[Signal][*CandidateIndex];
            let (Wire, Support, Air, Electrical) = Candidate.Claims.IndexSets();
            for (Values, Category) in [
                (Wire, 0usize),
                (Support, 1usize),
                (Air, 2usize),
                (Electrical, 3usize),
            ] {
                for Resource in Values {
                    CompletedClaims += 1;
                    if CompletedClaims % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                        return Some(Compatibility);
                    }
                    let Entry = Occupants.entry(*Resource).or_default();
                    match Category {
                        0 => Entry.Wire.push((SignalIndex, *CandidateIndex)),
                        1 => Entry.Support.push((SignalIndex, *CandidateIndex)),
                        2 => Entry.Air.push((SignalIndex, *CandidateIndex)),
                        _ => Entry.Electrical.push((SignalIndex, *CandidateIndex)),
                    }
                }
            }
        }
    }
    if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE").as_deref() == Ok("1") {
        eprintln!(
            "[debug] sparse compatibility occupants_ms={} claims={} resources={}",
            StartedAt.elapsed().as_millis(),
            CompletedClaims,
            Occupants.len(),
        );
    }
    let OccupantValues = Occupants.values().collect::<Vec<_>>();
    let mut ConflictPairs = RoutingThreadPool().install(|| {
        OccupantValues
            .into_par_iter()
            .map(|Entry| {
                let mut Result = Vec::new();
                let mut LocalPairCount = 0usize;
                for (FirstValues, SecondValues) in [
                    (&Entry.Wire, &Entry.Electrical),
                    (&Entry.Support, &Entry.Wire),
                    (&Entry.Support, &Entry.Air),
                    (&Entry.Air, &Entry.Wire),
                ] {
                    for First in FirstValues {
                        for Second in SecondValues {
                            LocalPairCount += 1;
                            if LocalPairCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                                return Result;
                            }
                            if First.0 == Second.0 {
                                continue;
                            }
                            let FirstCandidate = &Groups[&SignalNames[First.0]][First.1];
                            let SecondCandidate = &Groups[&SignalNames[Second.0]][Second.1];
                            if FirstCandidate.OwnerSignal == SecondCandidate.OwnerSignal
                                && !FirstCandidate
                                    .Claims
                                    .SameOwnerConflicts(&SecondCandidate.Claims)
                            {
                                continue;
                            }
                            let FirstEndpoint = ((First.0 as u64) << 16) | First.1 as u64;
                            let SecondEndpoint = ((Second.0 as u64) << 16) | Second.1 as u64;
                            let (Lower, Upper) = if FirstEndpoint < SecondEndpoint {
                                (FirstEndpoint, SecondEndpoint)
                            } else {
                                (SecondEndpoint, FirstEndpoint)
                            };
                            Result.push((Lower << 32) | Upper);
                        }
                    }
                }
                Result
            })
            .flatten()
            .collect::<Vec<_>>()
    });
    if let Some(CrossAirByWire) = CrossAirByWire {
        for (FirstWire, CrossValues) in CrossAirByWire.iter().enumerate() {
            let Some(FirstOccupants) = Occupants.get(&FirstWire) else {
                continue;
            };
            for (SecondWire, AirResource) in CrossValues {
                let Some(SecondOccupants) = Occupants.get(SecondWire) else {
                    continue;
                };
                for First in &FirstOccupants.Wire {
                    for Second in &SecondOccupants.Wire {
                        if First == Second || First.0 == Second.0 {
                            continue;
                        }
                        let FirstCandidate = &Groups[&SignalNames[First.0]][First.1];
                        let SecondCandidate = &Groups[&SignalNames[Second.0]][Second.1];
                        if FirstCandidate.OwnerSignal != SecondCandidate.OwnerSignal {
                            continue;
                        }
                        let FirstClaims = FirstCandidate.Claims.IndexSets();
                        let SecondClaims = SecondCandidate.Claims.IndexSets();
                        let EmergentAirConflicts =
                            [FirstClaims.0, FirstClaims.1, SecondClaims.0, SecondClaims.1]
                                .into_iter()
                                .any(|Values| Values.binary_search(AirResource).is_ok());
                        if !EmergentAirConflicts {
                            continue;
                        }
                        let FirstEndpoint = ((First.0 as u64) << 16) | First.1 as u64;
                        let SecondEndpoint = ((Second.0 as u64) << 16) | Second.1 as u64;
                        let (Lower, Upper) = if FirstEndpoint < SecondEndpoint {
                            (FirstEndpoint, SecondEndpoint)
                        } else {
                            (SecondEndpoint, FirstEndpoint)
                        };
                        ConflictPairs.push((Lower << 32) | Upper);
                    }
                }
            }
        }
    }
    if Deadline.Check() {
        return Some(Compatibility);
    }
    let CompletedPairs = ConflictPairs.len();
    RoutingThreadPool().install(|| ConflictPairs.par_sort_unstable());
    ConflictPairs.dedup();
    for (Index, EncodedPair) in ConflictPairs.iter().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Some(Compatibility);
        }
        let FirstEndpoint = EncodedPair >> 32;
        let SecondEndpoint = EncodedPair & u32::MAX as u64;
        let Decode = |Endpoint: u64| {
            (
                (Endpoint >> 16) as usize,
                (Endpoint & u16::MAX as u64) as usize,
            )
        };
        ClearSparseAccessConflict(
            &mut Compatibility,
            Decode(FirstEndpoint),
            Decode(SecondEndpoint),
        );
    }
    if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE").as_deref() == Ok("1") {
        eprintln!(
            "[debug] sparse compatibility complete_ms={} conflict_pairs={} unique_pairs={}",
            StartedAt.elapsed().as_millis(),
            CompletedPairs,
            ConflictPairs.len(),
        );
    }
    Some(Compatibility)
}

pub(super) fn EnforceArcConsistency(
    SignalNames: &[String],
    Compatibility: &CandidateCompatibility,
    Domains: &mut [CandidateDomain],
    Assigned: &[bool],
    Deadline: &RuntimeDeadline,
    FailureNet: &mut Option<String>,
) -> bool {
    loop {
        let DomainSnapshot = Domains.to_vec();
        let RevisedDomains = RoutingThreadPool().install(|| {
            (0..SignalNames.len())
                .into_par_iter()
                .map(|FirstSignalIndex| {
                    if Assigned[FirstSignalIndex] {
                        return None;
                    }
                    let mut Revised = DomainSnapshot[FirstSignalIndex].clone();
                    for FirstCandidateIndex in 0..Compatibility[FirstSignalIndex].len() {
                        if FirstCandidateIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                            return Some(Vec::new());
                        }
                        if !DomainContains(&DomainSnapshot[FirstSignalIndex], FirstCandidateIndex) {
                            continue;
                        }
                        let Unsupported = (0..SignalNames.len()).any(|SecondSignalIndex| {
                            !Assigned[SecondSignalIndex]
                                && SecondSignalIndex != FirstSignalIndex
                                && IntersectionCount(
                                    &DomainSnapshot[SecondSignalIndex],
                                    &Compatibility[FirstSignalIndex][FirstCandidateIndex]
                                        [SecondSignalIndex],
                                ) == 0
                        });
                        if Unsupported {
                            ClearDomainBit(&mut Revised, FirstCandidateIndex);
                        }
                    }
                    Some(Revised)
                })
                .collect::<Vec<_>>()
        });
        if Deadline.Check() {
            return false;
        }
        let mut Changed = false;
        for (SignalIndex, Revised) in RevisedDomains.into_iter().enumerate() {
            let Some(Revised) = Revised else {
                continue;
            };
            if DomainIsEmpty(&Revised) {
                if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE").as_deref() == Ok("1") {
                    let PreviousCount = DomainSnapshot[SignalIndex]
                        .iter()
                        .map(|Word| Word.count_ones() as usize)
                        .sum::<usize>();
                    let UnsupportedBy = (0..SignalNames.len())
                        .filter(|SecondSignalIndex| {
                            *SecondSignalIndex != SignalIndex
                                && !Assigned[*SecondSignalIndex]
                                && (0..Compatibility[SignalIndex].len())
                                    .filter(|CandidateIndex| {
                                        DomainContains(
                                            &DomainSnapshot[SignalIndex],
                                            *CandidateIndex,
                                        )
                                    })
                                    .all(|CandidateIndex| {
                                        IntersectionCount(
                                            &DomainSnapshot[*SecondSignalIndex],
                                            &Compatibility[SignalIndex][CandidateIndex]
                                                [*SecondSignalIndex],
                                        ) == 0
                                    })
                        })
                        .map(|Index| SignalNames[Index].as_str())
                        .collect::<Vec<_>>();
                    eprintln!(
                        "[debug] arc empty variable={} previous={} unsupported_by={:?}",
                        SignalNames[SignalIndex], PreviousCount, UnsupportedBy,
                    );
                }
                *FailureNet = Some(SignalNames[SignalIndex].clone());
                return false;
            }
            Changed |= Revised != Domains[SignalIndex];
            Domains[SignalIndex] = Revised;
        }
        if !Changed {
            return !Deadline.Check();
        }
    }
}

pub(super) fn DomainsIntersect(First: &CandidateDomain, Second: &CandidateDomain) -> bool {
    First
        .iter()
        .zip(Second)
        .any(|(FirstWord, SecondWord)| FirstWord & SecondWord != 0)
}

pub(super) fn EnforceIncrementalArcConsistency(
    SignalNames: &[String],
    Compatibility: &CandidateCompatibility,
    Domains: &mut [CandidateDomain],
    Assigned: &[bool],
    ConstraintNeighbors: &[Vec<usize>],
    ChangedSignalIndices: &[usize],
    Deadline: &RuntimeDeadline,
    FailureNet: &mut Option<String>,
) -> bool {
    let SignalCount = SignalNames.len();
    let mut Pending = VecDeque::<(usize, usize)>::new();
    let mut Queued = vec![false; SignalCount.saturating_mul(SignalCount)];
    let Enqueue = |First: usize,
                   Second: usize,
                   Pending: &mut VecDeque<(usize, usize)>,
                   Queued: &mut [bool]| {
        if First == Second || Assigned[First] || Assigned[Second] {
            return;
        }
        let Key = First * SignalCount + Second;
        if !Queued[Key] {
            Queued[Key] = true;
            Pending.push_back((First, Second));
        }
    };
    for Second in ChangedSignalIndices.iter().copied() {
        for First in ConstraintNeighbors[Second].iter().copied() {
            Enqueue(First, Second, &mut Pending, &mut Queued);
        }
    }
    let mut RevisionCount = 0usize;
    while let Some((First, Second)) = Pending.pop_front() {
        Queued[First * SignalCount + Second] = false;
        RevisionCount += 1;
        if RevisionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return false;
        }
        let mut Revised = false;
        for FirstCandidateIndex in 0..Compatibility[First].len() {
            if !DomainContains(&Domains[First], FirstCandidateIndex) {
                continue;
            }
            if !DomainsIntersect(
                &Domains[Second],
                &Compatibility[First][FirstCandidateIndex][Second],
            ) {
                ClearDomainBit(&mut Domains[First], FirstCandidateIndex);
                Revised = true;
            }
        }
        if !Revised {
            continue;
        }
        if DomainIsEmpty(&Domains[First]) {
            *FailureNet = Some(SignalNames[First].clone());
            return false;
        }
        for Other in ConstraintNeighbors[First].iter().copied() {
            if Other != Second {
                Enqueue(Other, First, &mut Pending, &mut Queued);
            }
        }
    }
    !Deadline.Check()
}
pub(super) fn RecordIndexedDeadEnd(
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
