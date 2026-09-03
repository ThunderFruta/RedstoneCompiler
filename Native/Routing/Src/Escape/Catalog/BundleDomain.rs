use super::*;

pub(in crate::Escape) type LayeredCatalogBundleDecodeMap =
    HashMap<(String, String), Vec<(String, String)>>;

pub(in crate::Escape) fn BuildUniqueLayeredCatalogBundleGroups(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    Deadline: &RuntimeDeadline,
) -> PyResult<
    Option<(
        BTreeMap<String, Vec<AssignmentCandidate>>,
        LayeredCatalogBundleDecodeMap,
    )>,
> {
    let AccessVariables = Groups
        .keys()
        .filter(|Variable| Variable.starts_with("__access_terminal__:"))
        .cloned()
        .collect::<BTreeSet<_>>();
    let mut ReferencedAccessVariables = BTreeSet::<String>::new();
    let mut BundledGroups = Groups
        .iter()
        .filter(|(Variable, _Values)| Variable.starts_with("__base_claim__:"))
        .map(|(Variable, Values)| (Variable.clone(), Values.clone()))
        .collect::<BTreeMap<_, _>>();
    let mut DecodeMap = LayeredCatalogBundleDecodeMap::new();
    for (Variable, Values) in Groups
        .iter()
        .filter(|(Variable, _Values)| Variable.starts_with("__route_guide__:"))
    {
        let mut BundledValues = Vec::with_capacity(Values.len());
        for Candidate in Values {
            if Deadline.Check() {
                return Ok(None);
            }
            let Some(Constraint) = Candidate.PoweredAccessConstraint.as_ref() else {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "compact guide bundle is missing its exact access certificate",
                ));
            };
            let mut AccessSelectionCombinations = Vec::new();
            for CandidateTuple in Constraint.PreferredAccessCandidateTuples.iter() {
                let mut AccessSelections = BTreeMap::<String, usize>::new();
                for (AccessVariable, AccessCandidateId) in CandidateTuple {
                    let Some(AccessCandidateIndex) =
                        Groups.get(AccessVariable).and_then(|Values| {
                            Values
                                .iter()
                                .position(|Value| Value.CandidateId == *AccessCandidateId)
                        })
                    else {
                        return Err(pyo3::exceptions::PyValueError::new_err(
                            "layered guide certificate references an unknown access candidate",
                        ));
                    };
                    if AccessSelections
                        .insert(AccessVariable.clone(), AccessCandidateIndex)
                        .is_some_and(|Previous| Previous != AccessCandidateIndex)
                    {
                        return Err(pyo3::exceptions::PyValueError::new_err(
                            "layered guide certificate assigns one access variable twice",
                        ));
                    }
                }
                AccessSelectionCombinations.push(AccessSelections);
            }
            AccessSelectionCombinations.sort();
            AccessSelectionCombinations.dedup();
            let CombinationCount = AccessSelectionCombinations.len();
            for (CombinationIndex, AccessSelections) in
                AccessSelectionCombinations.into_iter().enumerate()
            {
                let mut CombinedClaims = (*Candidate.Claims).clone();
                let mut CombinedRequirements = Candidate
                    .TemplateRequirements
                    .iter()
                    .filter(|(Name, _Value)| {
                        !Name.starts_with("access-stub:") && !Name.starts_with("access-portal:")
                    })
                    .cloned()
                    .collect::<Vec<_>>();
                let mut DecodedValues = vec![(Variable.clone(), Candidate.CandidateId.clone())];
                let mut MaterialCost = Candidate.MaterialCost;
                let mut FootprintGrowth = Candidate.FootprintGrowth;
                let mut Length = Candidate.Length;
                let mut BendCount = Candidate.BendCount;
                let mut ViaCount = Candidate.ViaCount;
                let mut SelfLegal = true;
                for (AccessVariable, AccessCandidateIndex) in AccessSelections {
                    let AccessCandidate = Groups
                        .get(&AccessVariable)
                        .and_then(|Candidates| Candidates.get(AccessCandidateIndex))
                        .ok_or_else(|| {
                            pyo3::exceptions::PyValueError::new_err(
                                "layered guide requirement references an unknown access candidate",
                            )
                        })?;
                    if AccessCandidate.OwnerSignal != Candidate.OwnerSignal {
                        return Err(pyo3::exceptions::PyValueError::new_err(
                            "layered guide and required access choice have different owners",
                        ));
                    }
                    let Some(SelfConflict) = CombinedClaims
                        .SameOwnerConflictsWithDeadline(&AccessCandidate.Claims, Deadline)
                    else {
                        return Ok(None);
                    };
                    if SelfConflict {
                        SelfLegal = false;
                        break;
                    }
                    if !CombinedClaims.UnionWithDeadline(&AccessCandidate.Claims, Deadline) {
                        return Ok(None);
                    }
                    CombinedRequirements.extend(
                        AccessCandidate
                            .TemplateRequirements
                            .iter()
                            .filter(|(Name, _Value)| {
                                !Name.starts_with("access-stub:")
                                    && !Name.starts_with("access-portal:")
                            })
                            .cloned(),
                    );
                    MaterialCost = MaterialCost.saturating_add(AccessCandidate.MaterialCost);
                    FootprintGrowth =
                        FootprintGrowth.saturating_add(AccessCandidate.FootprintGrowth);
                    Length = Length.saturating_add(AccessCandidate.Length);
                    BendCount = BendCount.saturating_add(AccessCandidate.BendCount);
                    ViaCount = ViaCount.saturating_add(AccessCandidate.ViaCount);
                    ReferencedAccessVariables.insert(AccessVariable.clone());
                    DecodedValues.push((AccessVariable, AccessCandidate.CandidateId.clone()));
                }
                if !SelfLegal {
                    continue;
                }
                CombinedRequirements.sort();
                if CombinedRequirements
                    .windows(2)
                    .any(|Pair| Pair[0].0 == Pair[1].0 && Pair[0].1 != Pair[1].1)
                {
                    return Err(pyo3::exceptions::PyValueError::new_err(
                        "layered bundle contains incompatible named requirements",
                    ));
                }
                CombinedRequirements.dedup();
                DecodedValues.sort();
                let BundleCandidateId = if CombinationCount == 1 {
                    Candidate.CandidateId.clone()
                } else {
                    format!("{}:bundle:{}", Candidate.CandidateId, CombinationIndex,)
                };
                DecodeMap.insert((Variable.clone(), BundleCandidateId.clone()), DecodedValues);
                BundledValues.push(AssignmentCandidate {
                    CandidateId: BundleCandidateId,
                    OwnerSignal: Candidate.OwnerSignal.clone(),
                    TemplateRequirements: Arc::new(CombinedRequirements),
                    ForbiddenCandidateIds: Arc::new(Vec::new()),
                    OrderedWire: Arc::new(Vec::new()),
                    PoweredAccessConstraint: None,
                    Claims: Arc::new(CombinedClaims),
                    MaterialCost,
                    FootprintGrowth,
                    Length,
                    BendCount,
                    ViaCount,
                });
            }
        }
        BundledGroups.insert(Variable.clone(), BundledValues);
    }
    for Variable in AccessVariables.difference(&ReferencedAccessVariables) {
        BundledGroups.insert(
            Variable.clone(),
            Groups
                .get(Variable)
                .expect("standalone access variable belongs to the source catalog")
                .clone(),
        );
    }
    Ok(Some((BundledGroups, DecodeMap)))
}

pub(in crate::Escape) fn EnumerateExactLayeredGuideAccessTuples(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    GuideVariable: &str,
    GuideCandidateIndex: usize,
    Deadline: &RuntimeDeadline,
) -> Option<Vec<Vec<(String, String)>>> {
    let GuideCandidate = Groups.get(GuideVariable)?.get(GuideCandidateIndex)?;
    let Constraint = GuideCandidate.PoweredAccessConstraint.as_ref()?;
    let TerminalVariables = Constraint
        .TerminalVariables
        .iter()
        .cloned()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let Domains = TerminalVariables
        .iter()
        .map(|Variable| {
            Groups.get(Variable).map(|Candidates| {
                Candidates
                    .iter()
                    .enumerate()
                    .filter(|(_CandidateIndex, Candidate)| {
                        Candidate.TemplateRequirements.iter().all(
                            |(CandidateName, CandidateValue)| {
                                GuideCandidate.TemplateRequirements.iter().all(
                                    |(GuideName, GuideValue)| {
                                        CandidateName != GuideName || CandidateValue == GuideValue
                                    },
                                )
                            },
                        )
                    })
                    .map(|(CandidateIndex, _Candidate)| CandidateIndex)
                    .collect::<Vec<_>>()
            })
        })
        .collect::<Option<Vec<_>>>()?;
    if Domains.iter().any(Vec::is_empty) {
        return Some(Vec::new());
    }
    let SignalNames = Groups.keys().cloned().collect::<Vec<_>>();
    let SignalIndexByName = SignalNames
        .iter()
        .enumerate()
        .map(|(Index, Variable)| (Variable.as_str(), Index))
        .collect::<HashMap<_, _>>();
    let GuideSignalIndex = *SignalIndexByName.get(GuideVariable)?;
    let mut Selection = vec![None; SignalNames.len()];
    Selection[GuideSignalIndex] = Some(GuideCandidateIndex);
    let mut SelectedAccess = Vec::<(usize, usize)>::new();
    let mut Result = Vec::<Vec<(String, String)>>::new();
    fn Search(
        Offset: usize,
        Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
        GuideVariable: &str,
        TerminalVariables: &[String],
        Domains: &[Vec<usize>],
        SignalNames: &[String],
        SignalIndexByName: &HashMap<&str, usize>,
        Selection: &mut [Option<usize>],
        SelectedAccess: &mut Vec<(usize, usize)>,
        Result: &mut Vec<Vec<(String, String)>>,
        Deadline: &RuntimeDeadline,
    ) -> Option<()> {
        if Deadline.Check() {
            return None;
        }
        let Some(Variable) = TerminalVariables.get(Offset) else {
            let mut FailureNet = None;
            if SelectionHasPoweredAccessWitnessExact(
                Groups,
                SignalNames,
                Selection,
                Deadline,
                &mut FailureNet,
            )? {
                let mut Tuple = SelectedAccess
                    .iter()
                    .map(|(SignalIndex, CandidateIndex)| {
                        (
                            SignalNames[*SignalIndex].clone(),
                            Groups[&SignalNames[*SignalIndex]][*CandidateIndex]
                                .CandidateId
                                .clone(),
                        )
                    })
                    .collect::<Vec<_>>();
                Tuple.sort();
                Result.push(Tuple);
            }
            return Some(());
        };
        let SignalIndex = *SignalIndexByName.get(Variable.as_str())?;
        for CandidateIndex in &Domains[Offset] {
            let Candidate = &Groups[Variable][*CandidateIndex];
            let SelfLegal = !Groups[GuideVariable]
                [Selection[*SignalIndexByName.get(GuideVariable)?]?]
            .Claims
            .SameOwnerConflictsWithDeadline(&Candidate.Claims, Deadline)?
                && SelectedAccess
                    .iter()
                    .all(|(PriorSignalIndex, PriorCandidateIndex)| {
                        !Candidate.Claims.SameOwnerConflicts(
                            &Groups[&SignalNames[*PriorSignalIndex]][*PriorCandidateIndex].Claims,
                        )
                    });
            if !SelfLegal {
                continue;
            }
            Selection[SignalIndex] = Some(*CandidateIndex);
            SelectedAccess.push((SignalIndex, *CandidateIndex));
            Search(
                Offset + 1,
                Groups,
                GuideVariable,
                TerminalVariables,
                Domains,
                SignalNames,
                SignalIndexByName,
                Selection,
                SelectedAccess,
                Result,
                Deadline,
            )?;
            SelectedAccess.pop();
            Selection[SignalIndex] = None;
        }
        Some(())
    }
    Search(
        0,
        Groups,
        GuideVariable,
        &TerminalVariables,
        &Domains,
        &SignalNames,
        &SignalIndexByName,
        &mut Selection,
        &mut SelectedAccess,
        &mut Result,
        Deadline,
    )?;
    Result.sort();
    Result.dedup();
    Some(Result)
}

pub(in crate::Escape) fn CloseLayeredCatalogWarmGuideTuples(
    Groups: &mut BTreeMap<String, Vec<AssignmentCandidate>>,
    WarmSelections: &[(String, String)],
    Deadline: &RuntimeDeadline,
) -> Option<BTreeSet<String>> {
    let WarmCandidateIdByVariable = WarmSelections.iter().cloned().collect::<HashMap<_, _>>();
    let SignalNames = Groups.keys().cloned().collect::<Vec<_>>();
    let SignalIndexByName = SignalNames
        .iter()
        .enumerate()
        .map(|(Index, Variable)| (Variable.as_str(), Index))
        .collect::<HashMap<_, _>>();
    let GuideVariables = SignalNames
        .iter()
        .filter(|Variable| Variable.starts_with("__route_guide__:"))
        .cloned()
        .collect::<Vec<_>>();
    let mut WarmTupleUpdates = Vec::<(String, usize, Vec<(String, String)>)>::new();
    let mut RepairGuideVariables = BTreeSet::<String>::new();

    for GuideVariable in &GuideVariables {
        if Deadline.Check() {
            return None;
        }
        let Some(GuideCandidateId) = WarmCandidateIdByVariable.get(GuideVariable) else {
            RepairGuideVariables.insert(GuideVariable.clone());
            continue;
        };
        let Some(GuideCandidateIndex) = Groups[GuideVariable]
            .iter()
            .position(|Candidate| Candidate.CandidateId == *GuideCandidateId)
        else {
            RepairGuideVariables.insert(GuideVariable.clone());
            continue;
        };
        let Some(Constraint) = Groups[GuideVariable][GuideCandidateIndex]
            .PoweredAccessConstraint
            .as_ref()
        else {
            RepairGuideVariables.insert(GuideVariable.clone());
            continue;
        };
        let mut WarmTuple = Vec::<(String, String)>::new();
        let mut Selection = vec![None; SignalNames.len()];
        Selection[*SignalIndexByName.get(GuideVariable.as_str())?] = Some(GuideCandidateIndex);
        let mut CompleteTuple = true;
        for TerminalVariable in Constraint.TerminalVariables.iter() {
            let Some(AccessCandidateId) = WarmCandidateIdByVariable.get(TerminalVariable) else {
                CompleteTuple = false;
                break;
            };
            let Some(AccessCandidateIndex) = Groups[TerminalVariable]
                .iter()
                .position(|Candidate| Candidate.CandidateId == *AccessCandidateId)
            else {
                CompleteTuple = false;
                break;
            };
            Selection[*SignalIndexByName.get(TerminalVariable.as_str())?] =
                Some(AccessCandidateIndex);
            WarmTuple.push((TerminalVariable.clone(), AccessCandidateId.clone()));
        }
        if !CompleteTuple {
            RepairGuideVariables.insert(GuideVariable.clone());
            continue;
        }
        WarmTuple.sort();
        WarmTuple.dedup();
        if Constraint
            .PreferredAccessCandidateTuples
            .iter()
            .any(|CandidateTuple| CandidateTuple == &WarmTuple)
        {
            continue;
        }
        let mut FailureNet = None;
        match SelectionHasPoweredAccessWitnessExact(
            Groups,
            &SignalNames,
            &Selection,
            Deadline,
            &mut FailureNet,
        ) {
            Some(true) => {
                WarmTupleUpdates.push((GuideVariable.clone(), GuideCandidateIndex, WarmTuple));
            }
            Some(false) => {
                RepairGuideVariables.insert(GuideVariable.clone());
            }
            None => return None,
        }
    }

    for (GuideVariable, GuideCandidateIndex, WarmTuple) in WarmTupleUpdates {
        let Constraint = Arc::make_mut(
            Groups
                .get_mut(&GuideVariable)?
                .get_mut(GuideCandidateIndex)?
                .PoweredAccessConstraint
                .as_mut()?,
        );
        let Tuples = Arc::make_mut(&mut Constraint.PreferredAccessCandidateTuples);
        Tuples.push(WarmTuple);
        Tuples.sort();
        Tuples.dedup();
    }

    let mut ExactTupleUpdates = Vec::<(String, usize, Vec<Vec<(String, String)>>)>::new();
    for GuideVariable in &RepairGuideVariables {
        for GuideCandidateIndex in 0..Groups[GuideVariable].len() {
            let Tuples = EnumerateExactLayeredGuideAccessTuples(
                Groups,
                GuideVariable,
                GuideCandidateIndex,
                Deadline,
            )?;
            ExactTupleUpdates.push((GuideVariable.clone(), GuideCandidateIndex, Tuples));
        }
    }
    for (GuideVariable, GuideCandidateIndex, Tuples) in ExactTupleUpdates {
        let Constraint = Arc::make_mut(
            Groups
                .get_mut(&GuideVariable)?
                .get_mut(GuideCandidateIndex)?
                .PoweredAccessConstraint
                .as_mut()?,
        );
        Constraint.PreferredAccessCandidateTuples = Arc::new(Tuples);
    }
    Some(RepairGuideVariables)
}
