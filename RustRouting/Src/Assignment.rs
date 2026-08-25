use crate::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Models::{AssignmentCandidate, ClaimMask, Position};
use crate::RoutingThreadPool;
use rayon::prelude::*;
use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};
use std::sync::atomic::{AtomicUsize, Ordering as AtomicOrdering};
use std::sync::Arc;

type CandidateDomain = Vec<u64>;
type CandidateCompatibility = Vec<Vec<Vec<Arc<CandidateDomain>>>>;

#[derive(Default)]
struct AccessResourceCandidates {
    Wire: Vec<(usize, usize)>,
    Support: Vec<(usize, usize)>,
    Air: Vec<(usize, usize)>,
    Electrical: Vec<(usize, usize)>,
}

pub(crate) fn ParseContractRequirements(Encoded: &str) -> Arc<Vec<(String, String)>> {
    Arc::new(
        Encoded
            .split(';')
            .filter_map(|Entry| {
                if Entry.is_empty() {
                    None
                } else {
                    let (Name, Value) = Entry.split_once('=').unwrap_or(("template", Entry));
                    Some((Name.to_string(), Value.to_string()))
                }
            })
            .collect(),
    )
}

fn TemplatesAreCompatible(First: &AssignmentCandidate, Second: &AssignmentCandidate) -> bool {
    First
        .TemplateRequirements
        .iter()
        .all(|(FirstName, FirstValue)| {
            Second
                .TemplateRequirements
                .iter()
                .all(|(SecondName, SecondValue)| {
                    FirstName != SecondName || FirstValue == SecondValue
                })
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

fn CertifiedAccessTupleIsAvailable(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    SignalNames: &[String],
    Domains: &[CandidateDomain],
    CandidateTuple: &[(String, String)],
) -> bool {
    CandidateTuple.iter().all(|(Variable, CandidateId)| {
        let Some(SignalIndex) = SignalNames.iter().position(|Signal| Signal == Variable) else {
            return false;
        };
        Groups[Variable]
            .iter()
            .position(|Candidate| Candidate.CandidateId == *CandidateId)
            .is_some_and(|CandidateIndex| DomainContains(&Domains[SignalIndex], CandidateIndex))
    })
}

fn GuideCandidateHasAvailableCertifiedTuple(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    SignalNames: &[String],
    Domains: &[CandidateDomain],
    GuideSignalIndex: usize,
    GuideCandidateIndex: usize,
) -> bool {
    Groups[&SignalNames[GuideSignalIndex]][GuideCandidateIndex]
        .PoweredAccessConstraint
        .as_ref()
        .is_some_and(|Constraint| {
            Constraint
                .PreferredAccessCandidateTuples
                .iter()
                .any(|CandidateTuple| {
                    CertifiedAccessTupleIsAvailable(
                        Groups,
                        SignalNames,
                        Domains,
                        CandidateTuple,
                    )
                })
        })
}

fn IsDetachedInternalGuideVariable(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    Variable: &str,
) -> bool {
    Variable.starts_with("__route_guide__:")
        && Groups.get(Variable).into_iter().flatten().any(|Candidate| {
            Candidate.PoweredAccessConstraint.as_ref().is_some_and(|Constraint| {
                Constraint.TerminalVariables.is_empty()
                    && Constraint.DetachedSeedAccessPaths.len() > 1
            })
        })
}

pub(crate) fn SelectionHasPoweredAccessWitnessExact(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    SignalNames: &[String],
    Selection: &[Option<usize>],
    Deadline: &RuntimeDeadline,
    FailureNet: &mut Option<String>,
) -> Option<bool> {
    let SignalIndexByName = SignalNames
        .iter()
        .enumerate()
        .map(|(Index, Signal)| (Signal.as_str(), Index))
        .collect::<HashMap<_, _>>();
    'Guides: for (GuideSignalIndex, GuideCandidateIndex) in Selection.iter().enumerate() {
        let Some(GuideCandidateIndex) = *GuideCandidateIndex else {
            continue;
        };
        let GuideCandidate = &Groups[&SignalNames[GuideSignalIndex]][GuideCandidateIndex];
        let Some(Constraint) = GuideCandidate.PoweredAccessConstraint.as_ref() else {
            continue;
        };
        if !Constraint.HasPoweredTreeWitness {
            *FailureNet = Some(SignalNames[GuideSignalIndex].clone());
            return Some(false);
        }
        if Deadline.Check() {
            *FailureNet = Some(SignalNames[GuideSignalIndex].clone());
            return None;
        }
        let mut SelectedAccessCandidates = Vec::with_capacity(Constraint.TerminalVariables.len());
        for Variable in Constraint.TerminalVariables.iter() {
            let Some(AccessSignalIndex) = SignalIndexByName.get(Variable.as_str()).copied() else {
                *FailureNet = Some(Variable.clone());
                return Some(false);
            };
            let Some(AccessCandidateIndex) = Selection[AccessSignalIndex] else {
                // This local higher-order constraint is not decidable until
                // its guide and every named access variable are bound.
                continue 'Guides;
            };
            SelectedAccessCandidates.push(
                &Groups[&SignalNames[AccessSignalIndex]][AccessCandidateIndex],
            );
        }
        let mut CombinedWire = GuideCandidate
            .OrderedWire
            .iter()
            .copied()
            .chain(
                SelectedAccessCandidates
                    .iter()
                    .flat_map(|Candidate| Candidate.OrderedWire.iter().copied()),
            )
            .collect::<HashSet<_>>();
        CombinedWire.extend(
            Constraint
                .DetachedSeedAccessPaths
                .iter()
                .flatten()
                .copied(),
        );
        let RootDomainIndex = Constraint.SourceTerminalVariable.as_deref().and_then(
            |SourceVariable| {
                Constraint
                    .TerminalVariables
                    .iter()
                    .position(|Variable| Variable == SourceVariable)
            },
        );
        let mut SourcePaths = Vec::<&[Position]>::new();
        let mut TargetPaths = Vec::<&[Position]>::new();
        if let Some(RootDomainIndex) = RootDomainIndex {
            SourcePaths.push(
                SelectedAccessCandidates[RootDomainIndex]
                    .OrderedWire
                    .as_slice(),
            );
            TargetPaths.extend(
                SelectedAccessCandidates
                    .iter()
                    .enumerate()
                    .filter(|(DomainIndex, _Candidate)| *DomainIndex != RootDomainIndex)
                    .map(|(_DomainIndex, Candidate)| Candidate.OrderedWire.as_slice()),
            );
            TargetPaths.extend(
                Constraint
                    .DetachedSeedAccessPaths
                    .iter()
                    .map(Vec::as_slice),
            );
        } else if let Some(SourceDetachedAnchorIndex) =
            Constraint.SourceDetachedAnchorIndex
        {
            let Some(SourcePath) = Constraint
                .DetachedSeedAccessPaths
                .get(SourceDetachedAnchorIndex)
            else {
                *FailureNet = Some(SignalNames[GuideSignalIndex].clone());
                return Some(false);
            };
            SourcePaths.push(SourcePath.as_slice());
            TargetPaths.extend(
                SelectedAccessCandidates
                    .iter()
                    .map(|Candidate| Candidate.OrderedWire.as_slice()),
            );
            TargetPaths.extend(
                Constraint
                    .DetachedSeedAccessPaths
                    .iter()
                    .enumerate()
                    .filter(|(Index, _Path)| *Index != SourceDetachedAnchorIndex)
                    .map(|(_Index, Path)| Path.as_slice()),
            );
        }
        if SourcePaths.is_empty() || TargetPaths.is_empty() {
            continue;
        }
        let Some(Source) = SourcePaths[0].first().copied() else {
            *FailureNet = Some(SignalNames[GuideSignalIndex].clone());
            return Some(false);
        };
        let TargetPositions = TargetPaths
            .iter()
            .filter_map(|Path| Path.first().copied())
            .collect::<BTreeSet<_>>();
        if TargetPositions.len() != TargetPaths.len() || !CombinedWire.contains(&Source) {
            *FailureNet = Some(SignalNames[GuideSignalIndex].clone());
            return Some(false);
        }
        let mut BestPowerByState = HashMap::<(Position, Position), u8>::new();
        BestPowerByState.insert((Source, (0, 0, 0)), 15);
        let mut Pending = VecDeque::from([(Source, (0, 0, 0), 15u8)]);
        let mut ReachedTargets = BTreeSet::new();
        let mut WorkCount = 0usize;
        while let Some((Current, PriorDirection, PowerRemaining)) = Pending.pop_front() {
            if WorkCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                *FailureNet = Some(SignalNames[GuideSignalIndex].clone());
                return None;
            }
            WorkCount = WorkCount.saturating_add(1);
            if TargetPositions.contains(&Current) {
                ReachedTargets.insert(Current);
                if ReachedTargets.len() == TargetPositions.len() {
                    break;
                }
            }
            for Next in Constraint
                .GraphAdjacency
                .get(&Current)
                .into_iter()
                .flatten()
                .copied()
            {
                if !CombinedWire.contains(&Next) {
                    continue;
                }
                let DirectionValue = (
                    Next.0 - Current.0,
                    Next.1 - Current.1,
                    Next.2 - Current.2,
                );
                let NextPower = if PriorDirection != (0, 0, 0)
                    && PriorDirection == DirectionValue
                    && DirectionValue.1 == 0
                    && DirectionValue.0.abs() + DirectionValue.2.abs() == 1
                {
                    15
                } else {
                    PowerRemaining.saturating_sub(1)
                };
                if NextPower == 0 {
                    continue;
                }
                let State = (Next, DirectionValue);
                let BestPower = BestPowerByState.entry(State).or_insert(0);
                if NextPower <= *BestPower {
                    continue;
                }
                *BestPower = NextPower;
                Pending.push_back((Next, DirectionValue, NextPower));
            }
        }
        if ReachedTargets.len() != TargetPositions.len() {
            *FailureNet = Some(SignalNames[GuideSignalIndex].clone());
            if std::env::var("RCS_DEBUG_EXACT_ACCESS_TUPLE").as_deref() == Ok("1")
                || std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE_SIGNAL").as_deref()
                    == Ok(GuideCandidate.OwnerSignal.as_str())
            {
                static DEBUG_ONCE: std::sync::Once = std::sync::Once::new();
                DEBUG_ONCE.call_once(|| {
                    eprintln!(
                        "[debug] exact selected guide/access tuple rejected guide={} source={:?} targets={:?} reached={:?} guide_wire={} access={:?} detached={:?}",
                        SignalNames[GuideSignalIndex],
                        Source,
                        TargetPositions,
                        ReachedTargets,
                        GuideCandidate.OrderedWire.len(),
                        SelectedAccessCandidates
                            .iter()
                            .map(|Candidate| (
                                Candidate.CandidateId.as_str(),
                                Candidate.OrderedWire.len(),
                                Candidate.OrderedWire.first(),
                                Candidate.OrderedWire.last(),
                            ))
                            .collect::<Vec<_>>(),
                        Constraint.DetachedSeedAccessPaths,
                    );
                });
            }
            return Some(false);
        }
    }
    Some(true)
}

fn SelectionHasPoweredAccessWitness(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    SignalNames: &[String],
    Selection: &[Option<usize>],
    Deadline: &RuntimeDeadline,
    FailureNet: &mut Option<String>,
) -> Option<bool> {
    if Groups
        .keys()
        .any(|Variable| Variable.starts_with("__route_guide__:"))
    {
        // A compact guide can have several certified access tuples.  The
        // flat named portal contracts intentionally preserve that disjunction,
        // but a complete assignment must still use one coherent exact tuple;
        // independently substituting same-portal stubs destroys the physical
        // certificate carried into selected-world materialization.  Check the
        // tuple identity here without treating the compact hint graph as a
        // complete powered-routing proof.  Unbound access variables remain
        // undecided during propagation and are resolved at the assignment
        // leaf by the same predicate.
        let SignalIndexByName = SignalNames
            .iter()
            .enumerate()
            .map(|(Index, Signal)| (Signal.as_str(), Index))
            .collect::<HashMap<_, _>>();
        for (GuideSignalIndex, GuideCandidateIndex) in Selection.iter().enumerate() {
            let Some(GuideCandidateIndex) = *GuideCandidateIndex else {
                continue;
            };
            let GuideCandidate = &Groups[&SignalNames[GuideSignalIndex]][GuideCandidateIndex];
            let Some(Constraint) = GuideCandidate.PoweredAccessConstraint.as_ref() else {
                continue;
            };
            let HasCompatibleCertifiedTuple = Constraint
                .PreferredAccessCandidateTuples
                .iter()
                .any(|CandidateTuple| {
                    CandidateTuple.iter().all(|(Variable, CandidateId)| {
                        let Some(AccessSignalIndex) =
                            SignalIndexByName.get(Variable.as_str()).copied()
                        else {
                            return false;
                        };
                        let Some(AccessCandidateIndex) = Selection[AccessSignalIndex] else {
                            return true;
                        };
                        Groups[Variable][AccessCandidateIndex].CandidateId == *CandidateId
                    })
                });
            if !HasCompatibleCertifiedTuple {
                *FailureNet = Some(SignalNames[GuideSignalIndex].clone());
                return Some(false);
            }
        }
        return Some(true);
    }
    SelectionHasPoweredAccessWitnessExact(
        Groups,
        SignalNames,
        Selection,
        Deadline,
        FailureNet,
    )
}

#[allow(clippy::too_many_arguments)]
fn GuideCandidateHasPoweredAccessBundle(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    SignalNames: &[String],
    Compatibility: &CandidateCompatibility,
    Domains: &[CandidateDomain],
    Selection: &[Option<usize>],
    GuideSignalIndex: usize,
    GuideCandidateIndex: usize,
    Deadline: &RuntimeDeadline,
    FailureNet: &mut Option<String>,
) -> Option<bool> {
    // See SelectionHasPoweredAccessWitness: the compact catalog repairs this
    // higher-order relation lazily without expanding guide-by-stub bundles.
    if SignalNames
        .iter()
        .any(|Variable| Variable.starts_with("__route_guide__:"))
    {
        return Some(true);
    }
    let GuideCandidate = &Groups[&SignalNames[GuideSignalIndex]][GuideCandidateIndex];
    let Some(Constraint) = GuideCandidate.PoweredAccessConstraint.as_ref() else {
        return Some(true);
    };
    let SignalIndexByName = SignalNames
        .iter()
        .enumerate()
        .map(|(Index, Signal)| (Signal.as_str(), Index))
        .collect::<HashMap<_, _>>();
    let AccessSignalIndices = Constraint
        .TerminalVariables
        .iter()
        .filter_map(|Variable| SignalIndexByName.get(Variable.as_str()).copied())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    if AccessSignalIndices.len()
        != Constraint
            .TerminalVariables
            .iter()
            .collect::<BTreeSet<_>>()
            .len()
    {
        *FailureNet = Some(SignalNames[GuideSignalIndex].clone());
        return Some(false);
    }
    let CandidateIndexById = AccessSignalIndices
        .iter()
        .map(|AccessSignalIndex| {
            (
                *AccessSignalIndex,
                Groups[&SignalNames[*AccessSignalIndex]]
                    .iter()
                    .enumerate()
                    .map(|(CandidateIndex, Candidate)| {
                        (Candidate.CandidateId.as_str(), CandidateIndex)
                    })
                    .collect::<HashMap<_, _>>(),
            )
        })
        .collect::<HashMap<_, _>>();
    let TupleIsAvailable = |CandidateTuple: &[(String, String)]| {
        let mut Choices = Vec::<(usize, usize)>::with_capacity(CandidateTuple.len());
        for (Variable, CandidateId) in CandidateTuple {
            let Some(AccessSignalIndex) = SignalIndexByName.get(Variable.as_str()).copied() else {
                return false;
            };
            let Some(AccessCandidateIndex) = CandidateIndexById[&AccessSignalIndex]
                .get(CandidateId.as_str())
                .copied()
            else {
                return false;
            };
            if !DomainContains(&Domains[AccessSignalIndex], AccessCandidateIndex)
                || !DomainContains(
                    &Compatibility[GuideSignalIndex][GuideCandidateIndex][AccessSignalIndex],
                    AccessCandidateIndex,
                )
                || Choices.iter().any(
                    |(PreviousSignalIndex, PreviousCandidateIndex)| {
                        !DomainContains(
                            Compatibility[AccessSignalIndex][AccessCandidateIndex]
                                [*PreviousSignalIndex]
                                .as_ref(),
                            *PreviousCandidateIndex,
                        )
                    },
                )
            {
                return false;
            }
            Choices.push((AccessSignalIndex, AccessCandidateIndex));
        }
        true
    };
    if Constraint
        .PreferredAccessCandidateTuples
        .iter()
        .any(|CandidateTuple| TupleIsAvailable(CandidateTuple))
    {
        return Some(true);
    }
    let mut TrialSelection = Selection.to_vec();
    TrialSelection[GuideSignalIndex] = Some(GuideCandidateIndex);
    fn Search(
        Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
        SignalNames: &[String],
        Compatibility: &CandidateCompatibility,
        Domains: &[CandidateDomain],
        AccessSignalIndices: &[usize],
        AccessOffset: usize,
        GuideSignalIndex: usize,
        GuideCandidateIndex: usize,
        TrialSelection: &mut [Option<usize>],
        Deadline: &RuntimeDeadline,
        FailureNet: &mut Option<String>,
    ) -> Option<bool> {
        if Deadline.Check() {
            return None;
        }
        let Some(AccessSignalIndex) = AccessSignalIndices.get(AccessOffset).copied() else {
            return SelectionHasPoweredAccessWitness(
                Groups,
                SignalNames,
                TrialSelection,
                Deadline,
                FailureNet,
            );
        };
        if let Some(SelectedCandidateIndex) = TrialSelection[AccessSignalIndex] {
            if !DomainContains(&Domains[AccessSignalIndex], SelectedCandidateIndex)
                || !DomainContains(
                    &Compatibility[GuideSignalIndex][GuideCandidateIndex][AccessSignalIndex],
                    SelectedCandidateIndex,
                )
            {
                return Some(false);
            }
            return Search(
                Groups,
                SignalNames,
                Compatibility,
                Domains,
                AccessSignalIndices,
                AccessOffset + 1,
                GuideSignalIndex,
                GuideCandidateIndex,
                TrialSelection,
                Deadline,
                FailureNet,
            );
        }
        for AccessCandidateIndex in 0..Groups[&SignalNames[AccessSignalIndex]].len() {
            if !DomainContains(&Domains[AccessSignalIndex], AccessCandidateIndex)
                || !DomainContains(
                    &Compatibility[GuideSignalIndex][GuideCandidateIndex][AccessSignalIndex],
                    AccessCandidateIndex,
                )
                || AccessSignalIndices[..AccessOffset].iter().any(
                    |PreviousSignalIndex| {
                        TrialSelection[*PreviousSignalIndex].is_some_and(
                            |PreviousCandidateIndex| {
                                !DomainContains(
                                    &Compatibility[AccessSignalIndex][AccessCandidateIndex]
                                        [*PreviousSignalIndex],
                                    PreviousCandidateIndex,
                                )
                            },
                        )
                    },
                )
            {
                continue;
            }
            TrialSelection[AccessSignalIndex] = Some(AccessCandidateIndex);
            match Search(
                Groups,
                SignalNames,
                Compatibility,
                Domains,
                AccessSignalIndices,
                AccessOffset + 1,
                GuideSignalIndex,
                GuideCandidateIndex,
                TrialSelection,
                Deadline,
                FailureNet,
            ) {
                Some(true) => {
                    TrialSelection[AccessSignalIndex] = None;
                    return Some(true);
                }
                Some(false) => {}
                None => {
                    TrialSelection[AccessSignalIndex] = None;
                    return None;
                }
            }
            TrialSelection[AccessSignalIndex] = None;
        }
        Some(false)
    }
    Search(
        Groups,
        SignalNames,
        Compatibility,
        Domains,
        &AccessSignalIndices,
        0,
        GuideSignalIndex,
        GuideCandidateIndex,
        &mut TrialSelection,
        Deadline,
        FailureNet,
    )
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

fn AccessContractIsLocalToVariable(Signal: &str, Candidate: &AssignmentCandidate) -> bool {
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

fn ClearSparseAccessConflict(
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

fn BuildSparseAccessCompatibility(
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
                    ExplicitConflictPairs.push((
                        (SignalIndex, *CandidateIndex),
                        ForbiddenEndpoint,
                    ));
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
                        let EmergentAirConflicts = [
                            FirstClaims.0,
                            FirstClaims.1,
                            SecondClaims.0,
                            SecondClaims.1,
                        ]
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

fn EnforceArcConsistency(
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

fn DomainsIntersect(First: &CandidateDomain, Second: &CandidateDomain) -> bool {
    First
        .iter()
        .zip(Second)
        .any(|(FirstWord, SecondWord)| FirstWord & SecondWord != 0)
}

fn EnforceIncrementalArcConsistency(
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
                    Constraint.PreferredAccessCandidateTuples.iter().any(
                        |CandidateTuple| {
                            CandidateTuple.iter().all(|(Variable, CandidateId)| {
                                let Some(TupleSignalIndex) = SignalNames
                                    .iter()
                                    .position(|Signal| Signal == Variable)
                                else {
                                    return false;
                                };
                                Groups[Variable]
                                    .iter()
                                    .position(|Candidate| {
                                        Candidate.CandidateId == *CandidateId
                                    })
                                    .is_some_and(|AccessCandidateIndex| {
                                        DomainContains(
                                            &Domains[TupleSignalIndex],
                                            AccessCandidateIndex,
                                        )
                                    })
                            })
                        },
                    )
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
                            let Some(TupleSignalIndex) = SignalNames
                                .iter()
                                .position(|Signal| Signal == Variable)
                            else {
                                return false;
                            };
                            if let Some(SelectedCandidateIndex) =
                                Selection[TupleSignalIndex]
                            {
                                Groups[Variable][SelectedCandidateIndex].CandidateId
                                    == *CandidateId
                            } else {
                                Groups[Variable]
                                    .iter()
                                    .position(|Candidate| {
                                        Candidate.CandidateId == *CandidateId
                                    })
                                    .is_some_and(|CandidateIndex| {
                                        DomainContains(
                                            &Domains[TupleSignalIndex],
                                            CandidateIndex,
                                        )
                                    })
                            }
                        })
                    })
                    .and_then(|CandidateTuple| {
                        CandidateTuple
                            .iter()
                            .find(|(Variable, _CandidateId)| {
                                Variable == &SignalNames[SignalIndex]
                            })
                            .map(|(_Variable, CandidateId)| CandidateId)
                    })
            });
        if let Some(CertifiedCandidateId) = CertifiedCandidateId {
            if let Some(CertifiedPosition) = CandidateIndices.iter().position(
                |(CandidateIndex, _Minimum, _Total)| {
                    Groups[&SignalNames[SignalIndex]][*CandidateIndex].CandidateId
                        == *CertifiedCandidateId
                },
            ) {
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
                        .position(|Candidate| {
                            Candidate.CandidateId == *AccessCandidateId
                        })
                    else {
                        BundleConsistent = false;
                        break;
                    };
                    if !DomainContains(
                        &BundleDomains[AccessSignalIndex],
                        AccessCandidateIndex,
                    ) || BundleSelection[AccessSignalIndex]
                        .is_some_and(|SelectedIndex| {
                            SelectedIndex != AccessCandidateIndex
                        })
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
                        if let Some(OtherCandidateIndex) =
                            BundleSelection[OtherSignalIndex]
                        {
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
                        .fetch_update(
                            AtomicOrdering::SeqCst,
                            AtomicOrdering::SeqCst,
                            |Value| {
                                Value
                                    .checked_add(NewlyAssignedAccessCount)
                                    .filter(|Next| *Next <= MaximumExpansionCount)
                            },
                        )
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
        match SelectionHasPoweredAccessWitness(
            Groups,
            SignalNames,
            Selection,
            Deadline,
            FailureNet,
        ) {
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

struct IndexedRootBranchOutcome {
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
fn SearchIndexedGuideBranchFromState(
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

fn BuildGreedyMaximalIndexedSelection(
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
        let SelectedCandidateIndex = if SignalNames[SignalIndex]
            .starts_with("__route_guide__:")
        {
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

fn BuildGreedyRepairIndexedSelection(
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

fn LayeredGuideIndexForAccessVariable(
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
fn SearchBestLayeredGuideAccessBundle(
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

fn SynchronizeLayeredGuideAccessSelection(
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

fn BuildBoundedMinConflictIndexedSelection(
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
                                    &Compatibility[SignalIndex][CandidateIndex]
                                        [*OtherSignalIndex],
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
            let WaveEnd = (RootCandidateOffset + CurrentWaveSize)
                .min(RootCandidateIndices.len());
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
                           Support: &[usize]| AssignmentCandidate {
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
