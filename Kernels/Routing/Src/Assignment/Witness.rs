//! Powered-access witnesses and shared assignment primitives.

use crate::Core::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Core::Models::{AssignmentCandidate, Position};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};
use std::sync::Arc;

pub(super) type CandidateDomain = Vec<u64>;
pub(super) type CandidateCompatibility = Vec<Vec<Vec<Arc<CandidateDomain>>>>;

#[derive(Default)]
pub(super) struct AccessResourceCandidates {
    pub(super) Wire: Vec<(usize, usize)>,
    pub(super) Support: Vec<(usize, usize)>,
    pub(super) Air: Vec<(usize, usize)>,
    pub(super) Electrical: Vec<(usize, usize)>,
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

pub(super) fn TemplatesAreCompatible(
    First: &AssignmentCandidate,
    Second: &AssignmentCandidate,
) -> bool {
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

pub(super) fn DomainWordCount(CandidateCount: usize) -> usize {
    CandidateCount.div_ceil(64)
}

pub(super) fn SetDomainBit(Domain: &mut CandidateDomain, CandidateIndex: usize) {
    Domain[CandidateIndex / 64] |= 1u64 << (CandidateIndex % 64);
}

pub(super) fn ClearDomainBit(Domain: &mut CandidateDomain, CandidateIndex: usize) {
    Domain[CandidateIndex / 64] &= !(1u64 << (CandidateIndex % 64));
}

pub(super) fn DomainContains(Domain: &CandidateDomain, CandidateIndex: usize) -> bool {
    Domain[CandidateIndex / 64] & (1u64 << (CandidateIndex % 64)) != 0
}

pub(super) fn DomainCount(Domain: &CandidateDomain) -> usize {
    Domain.iter().map(|Value| Value.count_ones() as usize).sum()
}

pub(super) fn DomainIsEmpty(Domain: &CandidateDomain) -> bool {
    Domain.iter().all(|Value| *Value == 0)
}

pub(super) fn CertifiedAccessTupleIsAvailable(
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

pub(super) fn GuideCandidateHasAvailableCertifiedTuple(
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
                    CertifiedAccessTupleIsAvailable(Groups, SignalNames, Domains, CandidateTuple)
                })
        })
}

pub(super) fn IsDetachedInternalGuideVariable(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    Variable: &str,
) -> bool {
    Variable.starts_with("__route_guide__:")
        && Groups.get(Variable).into_iter().flatten().any(|Candidate| {
            Candidate
                .PoweredAccessConstraint
                .as_ref()
                .is_some_and(|Constraint| {
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
            SelectedAccessCandidates
                .push(&Groups[&SignalNames[AccessSignalIndex]][AccessCandidateIndex]);
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
        CombinedWire.extend(Constraint.DetachedSeedAccessPaths.iter().flatten().copied());
        let RootDomainIndex =
            Constraint
                .SourceTerminalVariable
                .as_deref()
                .and_then(|SourceVariable| {
                    Constraint
                        .TerminalVariables
                        .iter()
                        .position(|Variable| Variable == SourceVariable)
                });
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
            TargetPaths.extend(Constraint.DetachedSeedAccessPaths.iter().map(Vec::as_slice));
        } else if let Some(SourceDetachedAnchorIndex) = Constraint.SourceDetachedAnchorIndex {
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
                let DirectionValue = (Next.0 - Current.0, Next.1 - Current.1, Next.2 - Current.2);
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

pub(super) fn SelectionHasPoweredAccessWitness(
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
            let HasCompatibleCertifiedTuple =
                Constraint
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
    SelectionHasPoweredAccessWitnessExact(Groups, SignalNames, Selection, Deadline, FailureNet)
}

#[allow(clippy::too_many_arguments)]
pub(super) fn GuideCandidateHasPoweredAccessBundle(
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
                || Choices
                    .iter()
                    .any(|(PreviousSignalIndex, PreviousCandidateIndex)| {
                        !DomainContains(
                            Compatibility[AccessSignalIndex][AccessCandidateIndex]
                                [*PreviousSignalIndex]
                                .as_ref(),
                            *PreviousCandidateIndex,
                        )
                    })
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
                || AccessSignalIndices[..AccessOffset]
                    .iter()
                    .any(|PreviousSignalIndex| {
                        TrialSelection[*PreviousSignalIndex].is_some_and(|PreviousCandidateIndex| {
                            !DomainContains(
                                &Compatibility[AccessSignalIndex][AccessCandidateIndex]
                                    [*PreviousSignalIndex],
                                PreviousCandidateIndex,
                            )
                        })
                    })
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
