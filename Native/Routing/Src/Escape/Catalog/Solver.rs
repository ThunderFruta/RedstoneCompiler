use super::*;

pub(in crate::Escape) fn SolveLayeredCatalogCandidateGroups(
    Groups: &mut BTreeMap<String, Vec<AssignmentCandidate>>,
    ResourceCount: usize,
    CrossAirByWire: Arc<Vec<Vec<(usize, usize)>>>,
    ExternalWarmSelections: Option<&[(String, String)]>,
    MaximumExpansionCount: usize,
    SharedExpansionCount: &std::sync::atomic::AtomicUsize,
    Deadline: &RuntimeDeadline,
) -> PyResult<RoutingAssignmentResult> {
    PrepareLayeredCatalogSolverPhase!(
        Groups,
        ResourceCount,
        CrossAirByWire,
        ExternalWarmSelections,
        MaximumExpansionCount,
        SharedExpansionCount,
        Deadline,
        AccessVariables,
        GuideVariables,
        WarmSelections,
        WarmCandidateIdByVariable,
        AccessChoicesByPortalRequirement,
        PrecomputedRepairGuideVariables,
        AccessChoicesByStubRequirement
    );
    let BundleDecodeMap = LayeredCatalogBundleDecodeMap::new();
    let AccessVariables = Groups
        .keys()
        .filter(|Variable| Variable.starts_with("__access_terminal__:"))
        .cloned()
        .collect::<Vec<_>>();
    GuideVariables = Groups
        .keys()
        .filter(|Variable| Variable.starts_with("__route_guide__:"))
        .cloned()
        .collect::<Vec<_>>();
    GuideVariables.sort_by_key(|Variable| {
        let CertifiedTupleCount = Groups[Variable]
            .iter()
            .filter_map(|Candidate| Candidate.PoweredAccessConstraint.as_ref())
            .map(|Constraint| Constraint.PreferredAccessCandidateTuples.len())
            .fold(0usize, usize::saturating_add);
        (
            CertifiedTupleCount,
            Groups[Variable].len(),
            Variable.clone(),
        )
    });
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered guide variable order {:?}",
            GuideVariables
                .iter()
                .map(|Variable| (Variable, Groups[Variable].len()))
                .collect::<Vec<_>>(),
        );
    }
    let OwnerIndexByName = Groups
        .values()
        .flatten()
        .map(|Candidate| Candidate.OwnerSignal.clone())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .enumerate()
        .map(|(Index, Owner)| (Owner, Index))
        .collect::<HashMap<_, _>>();
    let CandidateIndexByIdByVariable = Groups
        .iter()
        .map(|(Variable, Candidates)| {
            (
                Variable.clone(),
                Candidates
                    .iter()
                    .enumerate()
                    .map(|(CandidateIndex, Candidate)| {
                        (Candidate.CandidateId.clone(), CandidateIndex)
                    })
                    .collect::<HashMap<_, _>>(),
            )
        })
        .collect::<HashMap<_, _>>();
    let mut Context = LayeredCatalogSearchContext {
        Groups,
        CandidateIndexByIdByVariable: &CandidateIndexByIdByVariable,
        PreferredCandidateIdByVariable: &WarmCandidateIdByVariable,
        AccessChoicesByPortalRequirement: &AccessChoicesByPortalRequirement,
        AccessChoicesByStubRequirement: &AccessChoicesByStubRequirement,
        OwnerIndexByName: &OwnerIndexByName,
        SharedExpansionCount,
        MaximumExpansionCount,
        Deadline,
        ExpansionCount: 0,
        MaximumSelectedCount: 0,
        DeepestFailureDepth: 0,
        DeepestFailureNet: None,
        SearchVariant: 0,
        LocalMaximumExpansionCount: None,
        LocalBudgetExhausted: false,
        BudgetExhausted: false,
        DeadlineExceeded: false,
        FailureNet: None,
    };

    let mut State = LayeredCatalogSelectionState::New(ResourceCount, CrossAirByWire.clone());
    let mut WarmRequirementNames = Vec::new();
    let mut WarmWitnessIsExact = WarmSelections.len() == Context.Groups.len();
    let WarmBaseVariables = Context
        .Groups
        .keys()
        .filter(|Variable| Variable.starts_with("__base_claim__:"))
        .cloned()
        .collect::<Vec<_>>();
    let WarmGuideVariables = Context
        .Groups
        .keys()
        .filter(|Variable| Variable.starts_with("__route_guide__:"))
        .cloned()
        .collect::<Vec<_>>();
    let mut RepairGuideVariables = PrecomputedRepairGuideVariables;
    let mut PriorityRepairGuideVariables = RepairGuideVariables.clone();
    for Variable in &WarmBaseVariables {
        if !WarmWitnessIsExact {
            break;
        }
        let Some(CandidateId) = WarmCandidateIdByVariable.get(Variable) else {
            WarmWitnessIsExact = false;
            break;
        };
        let Some(CandidateIndex) = Context.Groups.get(Variable).and_then(|Values| {
            Values
                .iter()
                .position(|Candidate| Candidate.CandidateId == *CandidateId)
        }) else {
            WarmWitnessIsExact = false;
            break;
        };
        if !ConsumeLayeredCatalogExpansion(&mut Context, Variable)
            || !ApplyLayeredCatalogCandidate(
                &mut Context,
                &mut State,
                Variable,
                CandidateIndex,
                &mut WarmRequirementNames,
            )
        {
            WarmWitnessIsExact = false;
            break;
        }
    }
    if WarmWitnessIsExact {
        for Variable in &WarmGuideVariables {
            let Some(CandidateId) = WarmCandidateIdByVariable.get(Variable) else {
                RepairGuideVariables.insert(Variable.clone());
                continue;
            };
            let Some(CandidateIndex) = Context.Groups.get(Variable).and_then(|Values| {
                Values
                    .iter()
                    .position(|Candidate| Candidate.CandidateId == *CandidateId)
            }) else {
                RepairGuideVariables.insert(Variable.clone());
                continue;
            };
            let SelectedCheckpoint = State.SelectedOrder.len();
            let mut NewRequirementNames = Vec::new();
            if !ConsumeLayeredCatalogExpansion(&mut Context, Variable)
                || !ApplyLayeredCatalogCandidate(
                    &mut Context,
                    &mut State,
                    Variable,
                    CandidateIndex,
                    &mut NewRequirementNames,
                )
            {
                RollbackLayeredCatalogSelection(
                    &Context,
                    &mut State,
                    SelectedCheckpoint,
                    &mut NewRequirementNames,
                );
                RepairGuideVariables.insert(Variable.clone());
            } else {
                WarmRequirementNames.extend(NewRequirementNames);
            }
        }
    }
    if WarmWitnessIsExact {
        WarmWitnessIsExact = ApplyCompatibleWarmLayeredCatalogAccess(
            &mut Context,
            &mut State,
            &AccessVariables,
            &WarmCandidateIdByVariable,
            &mut WarmRequirementNames,
        );
    }
    if WarmWitnessIsExact {
        if RepairGuideVariables.is_empty() {
            WarmWitnessIsExact =
                SearchLayeredCatalogAccessByPortal(&mut Context, &mut State, &AccessVariables);
        } else {
            WarmWitnessIsExact = false;
        }
    }
    if WarmWitnessIsExact {
        for Variable in &WarmGuideVariables {
            if LayeredCatalogSelectedGuideHasPoweredWitness(&Context, &State, Variable)
                != Some(true)
            {
                RepairGuideVariables.insert(Variable.clone());
            }
        }
        if !RepairGuideVariables.is_empty() {
            PriorityRepairGuideVariables = RepairGuideVariables.clone();
            let mut FixedGuideState =
                LayeredCatalogSelectionState::New(ResourceCount, CrossAirByWire.clone());
            let mut FixedGuideRequirementNames = Vec::new();
            let mut FixedGuideReplayComplete = true;
            for Variable in WarmBaseVariables.iter().chain(WarmGuideVariables.iter()) {
                let Some(CandidateId) = WarmCandidateIdByVariable.get(Variable) else {
                    FixedGuideReplayComplete = false;
                    break;
                };
                let Some(CandidateIndex) = Context.Groups[Variable]
                    .iter()
                    .position(|Candidate| Candidate.CandidateId == *CandidateId)
                else {
                    FixedGuideReplayComplete = false;
                    break;
                };
                if !ConsumeLayeredCatalogExpansion(&mut Context, Variable)
                    || !ApplyLayeredCatalogCandidate(
                        &mut Context,
                        &mut FixedGuideState,
                        Variable,
                        CandidateIndex,
                        &mut FixedGuideRequirementNames,
                    )
                {
                    FixedGuideReplayComplete = false;
                    break;
                }
            }
            if FixedGuideReplayComplete {
                let mut FixedGuideOrder = WarmGuideVariables.clone();
                FixedGuideOrder.sort_by_key(|Variable| {
                    let CandidateIndex = FixedGuideState.SelectedByVariable[Variable];
                    let TupleCount = Context.Groups[Variable][CandidateIndex]
                        .PoweredAccessConstraint
                        .as_ref()
                        .map(|Constraint| Constraint.PreferredAccessCandidateTuples.len())
                        .unwrap_or(usize::MAX);
                    (TupleCount, Variable.clone())
                });
                let RemainingExpansionCount = Context.MaximumExpansionCount.saturating_sub(
                    Context
                        .SharedExpansionCount
                        .load(std::sync::atomic::Ordering::SeqCst),
                );
                Context.LocalMaximumExpansionCount = Some(
                    Context
                        .ExpansionCount
                        .saturating_add(RemainingExpansionCount / 2),
                );
                Context.LocalBudgetExhausted = false;
                WarmWitnessIsExact = SearchLayeredCatalogSelectedGuideTuples(
                    &mut Context,
                    &mut FixedGuideState,
                    &FixedGuideOrder,
                    &AccessVariables,
                );
                Context.LocalMaximumExpansionCount = None;
                Context.LocalBudgetExhausted = false;
                if WarmWitnessIsExact {
                    State = FixedGuideState;
                    RepairGuideVariables.clear();
                }
            }
            if !WarmWitnessIsExact {
                // Preserve the exact repair frontier.  Widening every guide
                // here destroys the useful pairwise witness and recreates the
                // full Cartesian search.  The repair loop below grows this set
                // from concrete powered or capacity failures when necessary.
                if let Some(FailureGuideVariable) = [
                    Context.DeepestFailureNet.as_deref(),
                    Context.FailureNet.as_deref(),
                ]
                .into_iter()
                .filter_map(|FailureVariable| {
                    LayeredCatalogGuideVariableForFailure(Context.Groups, FailureVariable)
                })
                .find(|Variable| !RepairGuideVariables.contains(Variable))
                {
                    RepairGuideVariables.insert(FailureGuideVariable);
                }
                Context.FailureNet = None;
            }
        }
    }
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered warm tuple repair {:?}",
            RepairGuideVariables
                .iter()
                .map(|Variable| {
                    let CandidateId = WarmCandidateIdByVariable.get(Variable);
                    let TupleCount = CandidateId
                        .and_then(|CandidateId| {
                            Context.Groups[Variable]
                                .iter()
                                .find(|Candidate| Candidate.CandidateId == *CandidateId)
                        })
                        .and_then(|Candidate| Candidate.PoweredAccessConstraint.as_ref())
                        .map(|Constraint| Constraint.PreferredAccessCandidateTuples.len())
                        .unwrap_or(0);
                    (Variable, TupleCount)
                })
                .collect::<Vec<_>>(),
        );
    }
    if !WarmWitnessIsExact && !Context.BudgetExhausted && !Context.DeadlineExceeded {
        if let Some(FailureGuideVariable) =
            LayeredCatalogGuideVariableForFailure(Context.Groups, Context.FailureNet.as_deref())
        {
            RepairGuideVariables.insert(FailureGuideVariable);
        }
    }
    let RunGlobalParallelRepair =
        !WarmWitnessIsExact && RepairGuideVariables.len() == WarmGuideVariables.len();
    while !WarmWitnessIsExact
        && !RepairGuideVariables.is_empty()
        && !RunGlobalParallelRepair
        && !Context.BudgetExhausted
        && !Context.DeadlineExceeded
    {
        if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
            eprintln!(
                "native layered repair iteration variables={:?} shared_expansions={}",
                RepairGuideVariables,
                Context
                    .SharedExpansionCount
                    .load(std::sync::atomic::Ordering::SeqCst),
            );
        }
        State = LayeredCatalogSelectionState::New(ResourceCount, CrossAirByWire.clone());
        WarmRequirementNames.clear();
        Context.FailureNet = None;
        let mut FixedWitnessComplete = true;
        for Variable in &WarmBaseVariables {
            let Some(CandidateId) = WarmCandidateIdByVariable.get(Variable) else {
                FixedWitnessComplete = false;
                Context.FailureNet = Some(Variable.clone());
                break;
            };
            let Some(CandidateIndex) = Context.Groups[Variable]
                .iter()
                .position(|Candidate| Candidate.CandidateId == *CandidateId)
            else {
                FixedWitnessComplete = false;
                Context.FailureNet = Some(Variable.clone());
                break;
            };
            if !ConsumeLayeredCatalogExpansion(&mut Context, Variable)
                || !ApplyLayeredCatalogCandidate(
                    &mut Context,
                    &mut State,
                    Variable,
                    CandidateIndex,
                    &mut WarmRequirementNames,
                )
            {
                FixedWitnessComplete = false;
                Context.FailureNet = Some(Variable.clone());
                break;
            }
        }
        let RepairCountBeforeFixedGuides = RepairGuideVariables.len();
        if FixedWitnessComplete {
            for Variable in &WarmGuideVariables {
                if RepairGuideVariables.contains(Variable) {
                    continue;
                }
                let Some(CandidateId) = WarmCandidateIdByVariable.get(Variable) else {
                    RepairGuideVariables.insert(Variable.clone());
                    continue;
                };
                let Some(CandidateIndex) = Context.Groups[Variable]
                    .iter()
                    .position(|Candidate| Candidate.CandidateId == *CandidateId)
                else {
                    RepairGuideVariables.insert(Variable.clone());
                    continue;
                };
                let SelectedCheckpoint = State.SelectedOrder.len();
                let mut NewRequirementNames = Vec::new();
                if !ConsumeLayeredCatalogExpansion(&mut Context, Variable)
                    || !ApplyLayeredCatalogCandidate(
                        &mut Context,
                        &mut State,
                        Variable,
                        CandidateIndex,
                        &mut NewRequirementNames,
                    )
                {
                    RollbackLayeredCatalogSelection(
                        &Context,
                        &mut State,
                        SelectedCheckpoint,
                        &mut NewRequirementNames,
                    );
                    RepairGuideVariables.insert(Variable.clone());
                } else {
                    WarmRequirementNames.extend(NewRequirementNames);
                }
            }
        }
        if !FixedWitnessComplete {
            break;
        }
        let RepairAccessVariables =
            LayeredCatalogAccessVariablesForGuides(&Context, &RepairGuideVariables);
        let FixedAccessVariables = AccessVariables
            .iter()
            .filter(|Variable| !RepairAccessVariables.contains(*Variable))
            .cloned()
            .collect::<Vec<_>>();
        FixedWitnessComplete = ApplyCompatibleWarmLayeredCatalogAccess(
            &mut Context,
            &mut State,
            &FixedAccessVariables,
            &WarmCandidateIdByVariable,
            &mut WarmRequirementNames,
        );
        if !FixedWitnessComplete {
            break;
        }
        if RepairGuideVariables.len() != RepairCountBeforeFixedGuides {
            continue;
        }
        let mut RepairGuideOrder = GuideVariables
            .iter()
            .filter(|Variable| RepairGuideVariables.contains(*Variable))
            .cloned()
            .collect::<Vec<_>>();
        RepairGuideOrder.sort_by_key(|Variable| {
            (
                usize::from(!PriorityRepairGuideVariables.contains(Variable)),
                GuideVariables
                    .iter()
                    .position(|CandidateVariable| CandidateVariable == Variable)
                    .unwrap_or(usize::MAX),
            )
        });
        Context.DeepestFailureDepth = 0;
        Context.DeepestFailureNet = None;
        Context.LocalBudgetExhausted = false;
        let SharedExpansionCountBeforeRepair = Context
            .SharedExpansionCount
            .load(std::sync::atomic::Ordering::SeqCst);
        let RemainingExpansionCount = Context
            .MaximumExpansionCount
            .saturating_sub(SharedExpansionCountBeforeRepair);
        let FixedGuideCount = WarmGuideVariables
            .len()
            .saturating_sub(RepairGuideVariables.len());
        let RepairExpansionAllowance = RemainingExpansionCount
            .checked_div(FixedGuideCount.saturating_add(1))
            .unwrap_or(0)
            .max(128);
        Context.LocalMaximumExpansionCount = Some(
            Context
                .ExpansionCount
                .saturating_add(RepairExpansionAllowance),
        );
        WarmWitnessIsExact = SearchLayeredCatalogGuidesByPortal(
            &mut Context,
            &mut State,
            &RepairGuideOrder,
            &AccessVariables,
        );
        Context.LocalMaximumExpansionCount = None;
        if WarmWitnessIsExact {
            for Variable in &WarmGuideVariables {
                if LayeredCatalogSelectedGuideHasPoweredWitness(&Context, &State, Variable)
                    != Some(true)
                {
                    RepairGuideVariables.insert(Variable.clone());
                }
            }
            if RepairGuideVariables
                .iter()
                .any(|Variable| !RepairGuideOrder.contains(Variable))
            {
                WarmWitnessIsExact = false;
            }
        }
        if WarmWitnessIsExact || Context.BudgetExhausted || Context.DeadlineExceeded {
            break;
        }
        let FailureGuideVariable = [
            Context.DeepestFailureNet.as_deref(),
            Context.FailureNet.as_deref(),
        ]
        .into_iter()
        .filter_map(|FailureVariable| {
            LayeredCatalogGuideVariableForFailure(Context.Groups, FailureVariable)
        })
        .find(|Variable| !RepairGuideVariables.contains(Variable))
        .or_else(|| LayeredCatalogBlockingSelectedGuide(&Context, &State, &RepairGuideVariables));
        let Some(FailureGuideVariable) = FailureGuideVariable else {
            break;
        };
        if !RepairGuideVariables.insert(FailureGuideVariable) {
            break;
        }
    }
    if WarmWitnessIsExact {
        let ExpansionCount = SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
        let mut SelectedCandidateIds = State
            .SelectedByVariable
            .iter()
            .flat_map(|(Variable, CandidateIndex)| {
                let CandidateId = &Context.Groups[Variable][*CandidateIndex].CandidateId;
                BundleDecodeMap
                    .get(&(Variable.clone(), CandidateId.clone()))
                    .cloned()
                    .unwrap_or_else(|| vec![(Variable.clone(), CandidateId.clone())])
            })
            .collect::<Vec<_>>();
        SelectedCandidateIds.sort();
        SelectedCandidateIds.dedup();
        return Ok(RoutingAssignmentResult {
            Success: true,
            SelectedCandidateIds,
            ExpansionCount,
            BudgetExhausted: false,
            DeadlineExceeded: false,
            CompletedWork: ExpansionCount,
            FailureNet: None,
            ConflictSignals: Vec::new(),
            ConflictResourceIndices: Vec::new(),
            PairwiseIncompatibleSignals: Vec::new(),
            PairwiseCompatibilityComplete: true,
        });
    }
    if Context.BudgetExhausted || Context.DeadlineExceeded {
        let ExpansionCount = SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
        return Ok(RoutingAssignmentResult {
            Success: false,
            SelectedCandidateIds: Vec::new(),
            ExpansionCount,
            BudgetExhausted: Context.BudgetExhausted,
            DeadlineExceeded: Context.DeadlineExceeded,
            CompletedWork: ExpansionCount,
            FailureNet: Context.FailureNet.clone(),
            ConflictSignals: Vec::new(),
            ConflictResourceIndices: Vec::new(),
            PairwiseIncompatibleSignals: Vec::new(),
            PairwiseCompatibilityComplete: true,
        });
    }
    RollbackLayeredCatalogSelection(&Context, &mut State, 0, &mut WarmRequirementNames);
    Context.FailureNet = None;
    let BaseVariables = Groups
        .keys()
        .filter(|Variable| Variable.starts_with("__base_claim__:"))
        .cloned()
        .collect::<Vec<_>>();
    let mut BaseComplete = true;
    for Variable in &BaseVariables {
        let mut NewRequirementNames = Vec::new();
        if Groups[Variable].len() != 1
            || !ApplyLayeredCatalogCandidate(
                &mut Context,
                &mut State,
                Variable,
                0,
                &mut NewRequirementNames,
            )
        {
            Context.FailureNet = Some(Variable.clone());
            BaseComplete = false;
            break;
        }
    }
    let mut Success = false;
    if BaseComplete && !RunGlobalParallelRepair {
        Success = SearchLayeredCatalogGuidesByPortal(
            &mut Context,
            &mut State,
            &GuideVariables,
            &AccessVariables,
        );
    }
    if BaseComplete && RunGlobalParallelRepair {
        GuideVariables.sort_by_key(|Variable| {
            (
                usize::from(!PriorityRepairGuideVariables.contains(Variable)),
                Groups[Variable].len(),
                Variable.clone(),
            )
        });
        if let Some(RootVariable) = GuideVariables.first().cloned() {
            let RootVariableIndex = GuideVariables
                .iter()
                .position(|Variable| Variable == &RootVariable)
                .expect("selected root guide belongs to guide order");
            let mut RemainingGuideVariables = GuideVariables.clone();
            RemainingGuideVariables.remove(RootVariableIndex);
            let RemainingMemberBudget = MaximumExpansionCount
                .saturating_sub(SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst));
            let WaveSize = RoutingThreadPool()
                .current_num_threads()
                .max(1)
                .min((RemainingMemberBudget / 1_500).max(1));
            let RootCandidateCount = Groups[&RootVariable].len();
            let mut FirstWaveIndices = (0..WaveSize.min(RootCandidateCount))
                .map(|SampleIndex| {
                    SampleIndex.saturating_mul(RootCandidateCount)
                        / WaveSize.min(RootCandidateCount).max(1)
                })
                .collect::<Vec<_>>();
            FirstWaveIndices.sort_unstable();
            FirstWaveIndices.dedup();
            let FirstWaveIndexSet = FirstWaveIndices.iter().copied().collect::<HashSet<_>>();
            let mut RootCandidateIndices = FirstWaveIndices
                .into_iter()
                .chain(
                    (0..RootCandidateCount)
                        .filter(|CandidateIndex| !FirstWaveIndexSet.contains(CandidateIndex)),
                )
                .collect::<Vec<_>>();
            if let Some(PreferredRootCandidateIndex) = WarmCandidateIdByVariable
                .get(&RootVariable)
                .and_then(|CandidateId| {
                    CandidateIndexByIdByVariable[&RootVariable].get(CandidateId)
                })
                .copied()
            {
                if let Some(PreferredPosition) = RootCandidateIndices
                    .iter()
                    .position(|CandidateIndex| *CandidateIndex == PreferredRootCandidateIndex)
                {
                    RootCandidateIndices[..=PreferredPosition].rotate_right(1);
                }
            }
            let EffectiveWaveSize = WaveSize.min(RootCandidateIndices.len()).max(1);
            let ShallowRootBranchExpansionAllowance = 128usize.min(RemainingMemberBudget.max(1));
            let mut PendingRootCandidates = RootCandidateIndices
                .into_iter()
                .map(|CandidateIndex| {
                    (
                        CandidateIndex,
                        ShallowRootBranchExpansionAllowance,
                        false,
                        Vec::<String>::new(),
                    )
                })
                .collect::<std::collections::VecDeque<_>>();
            let mut DeferredDeepCandidates = Vec::<(usize, usize, Option<String>)>::new();
            let mut AnyLocalBudgetExhausted = false;
            'RootWaves: while !PendingRootCandidates.is_empty() {
                let CurrentWaveSize = EffectiveWaveSize;
                let Wave = (0..CurrentWaveSize)
                    .filter_map(|_Index| PendingRootCandidates.pop_front())
                    .collect::<Vec<_>>();
                let Outcomes = RoutingThreadPool().install(|| {
                    Wave.par_iter()
                        .map(
                            |(CandidateIndex, BranchExpansionAllowance, IsDeep, PriorityGuides)| {
                                let mut BranchRemainingGuideVariables =
                                    RemainingGuideVariables.clone();
                                for PriorityVariable in PriorityGuides.iter().rev() {
                                    if let Some(PriorityVariableIndex) =
                                        BranchRemainingGuideVariables
                                            .iter()
                                            .position(|Variable| Variable == PriorityVariable)
                                    {
                                        let PriorityVariable = BranchRemainingGuideVariables
                                            .remove(PriorityVariableIndex);
                                        BranchRemainingGuideVariables.insert(0, PriorityVariable);
                                    }
                                }
                                let mut BranchContext = LayeredCatalogSearchContext {
                                    Groups,
                                    CandidateIndexByIdByVariable: &CandidateIndexByIdByVariable,
                                    PreferredCandidateIdByVariable: &WarmCandidateIdByVariable,
                                    AccessChoicesByPortalRequirement:
                                        &AccessChoicesByPortalRequirement,
                                    AccessChoicesByStubRequirement: &AccessChoicesByStubRequirement,
                                    OwnerIndexByName: &OwnerIndexByName,
                                    SharedExpansionCount,
                                    MaximumExpansionCount,
                                    Deadline,
                                    ExpansionCount: 0,
                                    MaximumSelectedCount: State.SelectedByVariable.len(),
                                    DeepestFailureDepth: 0,
                                    DeepestFailureNet: None,
                                    SearchVariant: CandidateIndex.wrapping_mul(17),
                                    LocalMaximumExpansionCount: Some(*BranchExpansionAllowance),
                                    LocalBudgetExhausted: false,
                                    BudgetExhausted: false,
                                    DeadlineExceeded: false,
                                    FailureNet: None,
                                };
                                let mut BranchState = State.clone();
                                let mut NewRequirementNames = Vec::new();
                                let BranchSuccess = ConsumeLayeredCatalogExpansion(
                                    &mut BranchContext,
                                    &RootVariable,
                                ) && ApplyLayeredCatalogCandidate(
                                    &mut BranchContext,
                                    &mut BranchState,
                                    &RootVariable,
                                    *CandidateIndex,
                                    &mut NewRequirementNames,
                                ) && {
                                    let mut PortalRequirements = Groups[&RootVariable]
                                        [*CandidateIndex]
                                        .TemplateRequirements
                                        .iter()
                                        .filter(|(Name, _Value)| Name.starts_with("access-portal:"))
                                        .cloned()
                                        .collect::<Vec<_>>();
                                    PortalRequirements.sort_by_key(|Requirement| {
                                        (
                                            AccessChoicesByPortalRequirement
                                                .get(Requirement)
                                                .map(Vec::len)
                                                .unwrap_or(0),
                                            Requirement.clone(),
                                        )
                                    });
                                    SearchLayeredCatalogPortalChoices(
                                        &mut BranchContext,
                                        &mut BranchState,
                                        &RootVariable,
                                        *CandidateIndex,
                                        &PortalRequirements,
                                        0,
                                        &BranchRemainingGuideVariables,
                                        &AccessVariables,
                                    )
                                };
                                (
                                    *CandidateIndex,
                                    *IsDeep,
                                    PriorityGuides.clone(),
                                    BranchSuccess,
                                    BranchState,
                                    BranchContext.BudgetExhausted,
                                    BranchContext.DeadlineExceeded,
                                    BranchContext.FailureNet,
                                    BranchContext.ExpansionCount,
                                    BranchContext.MaximumSelectedCount,
                                    BranchContext.DeepestFailureDepth,
                                    BranchContext.DeepestFailureNet,
                                    BranchContext.LocalBudgetExhausted,
                                )
                            },
                        )
                        .collect::<Vec<_>>()
                });
                for (
                    BranchCandidateIndex,
                    BranchWasDeep,
                    _BranchPriorityGuides,
                    BranchSuccess,
                    BranchState,
                    BranchBudgetExhausted,
                    BranchDeadlineExceeded,
                    BranchFailureNet,
                    _BranchExpansionCount,
                    BranchMaximumSelectedCount,
                    _BranchDeepestFailureDepth,
                    BranchDeepestFailureNet,
                    BranchLocalBudgetExhausted,
                ) in Outcomes
                {
                    AnyLocalBudgetExhausted |= BranchLocalBudgetExhausted;
                    if BranchSuccess {
                        State = BranchState;
                        Success = true;
                        break 'RootWaves;
                    }
                    if !BranchWasDeep && BranchLocalBudgetExhausted {
                        DeferredDeepCandidates.push((
                            BranchMaximumSelectedCount,
                            BranchCandidateIndex,
                            BranchDeepestFailureNet
                                .clone()
                                .filter(|Variable| Variable.starts_with("__route_guide__:")),
                        ));
                    }
                    if Context.FailureNet.is_none() {
                        Context.FailureNet = BranchFailureNet;
                    }
                    Context.BudgetExhausted |= BranchBudgetExhausted;
                    Context.DeadlineExceeded |= BranchDeadlineExceeded;
                }
                if SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst)
                    >= MaximumExpansionCount
                {
                    Context.BudgetExhausted = true;
                }
                if Context.BudgetExhausted || Context.DeadlineExceeded {
                    break;
                }
                if PendingRootCandidates.is_empty() {
                    if !DeferredDeepCandidates.is_empty() {
                        DeferredDeepCandidates.sort_by_key(
                            |(Depth, CandidateIndex, _PriorityGuide)| {
                                (std::cmp::Reverse(*Depth), *CandidateIndex)
                            },
                        );
                        DeferredDeepCandidates.dedup_by_key(
                            |(_Depth, CandidateIndex, _PriorityGuide)| *CandidateIndex,
                        );
                        let RemainingGlobalExpansions = MaximumExpansionCount.saturating_sub(
                            SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst),
                        );
                        let DeepCandidateCount = DeferredDeepCandidates.len().min(4);
                        let DeepBranchExpansionAllowance = RemainingGlobalExpansions
                            .checked_div(DeepCandidateCount.max(1))
                            .unwrap_or(0);
                        if DeepBranchExpansionAllowance > ShallowRootBranchExpansionAllowance {
                            for (_Depth, CandidateIndex, PriorityGuide) in
                                DeferredDeepCandidates.drain(..DeepCandidateCount)
                            {
                                PendingRootCandidates.push_back((
                                    CandidateIndex,
                                    DeepBranchExpansionAllowance,
                                    true,
                                    PriorityGuide.into_iter().collect(),
                                ));
                            }
                        }
                        DeferredDeepCandidates.clear();
                    }
                }
            }
            if !Success && AnyLocalBudgetExhausted {
                Context.BudgetExhausted = true;
            }
        } else {
            Success =
                SearchLayeredCatalogAccessByPortal(&mut Context, &mut State, &AccessVariables);
        }
    }
    let ExpansionCount = SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
    let mut SelectedCandidateIds = State
        .SelectedByVariable
        .iter()
        .flat_map(|(Variable, CandidateIndex)| {
            let CandidateId = &Groups[Variable][*CandidateIndex].CandidateId;
            BundleDecodeMap
                .get(&(Variable.clone(), CandidateId.clone()))
                .cloned()
                .unwrap_or_else(|| vec![(Variable.clone(), CandidateId.clone())])
        })
        .collect::<Vec<_>>();
    SelectedCandidateIds.sort();
    SelectedCandidateIds.dedup();
    let Assignment = RoutingAssignmentResult {
        Success,
        SelectedCandidateIds,
        ExpansionCount,
        BudgetExhausted: Context.BudgetExhausted,
        DeadlineExceeded: Context.DeadlineExceeded || Deadline.WasExceeded(),
        CompletedWork: ExpansionCount,
        FailureNet: Context.FailureNet,
        ConflictSignals: Vec::new(),
        ConflictResourceIndices: Vec::new(),
        PairwiseIncompatibleSignals: Vec::new(),
        PairwiseCompatibilityComplete: false,
    };
    Ok(Assignment)
}
