use super::*;

#[derive(Clone)]
pub(in crate::Escape) struct LayeredCatalogClaimOccupancy {
    pub(in crate::Escape) Wire: Vec<usize>,
    pub(in crate::Escape) Support: Vec<usize>,
    pub(in crate::Escape) Air: Vec<usize>,
    pub(in crate::Escape) Electrical: Vec<usize>,
    pub(in crate::Escape) WireByOwner: HashMap<(usize, usize), usize>,
    pub(in crate::Escape) ElectricalByOwner: HashMap<(usize, usize), usize>,
    pub(in crate::Escape) CrossAirByWire: Arc<Vec<Vec<(usize, usize)>>>,
}

impl LayeredCatalogClaimOccupancy {
    pub(in crate::Escape) fn New(
        ResourceCount: usize,
        CrossAirByWire: Arc<Vec<Vec<(usize, usize)>>>,
    ) -> Self {
        Self {
            Wire: vec![0; ResourceCount],
            Support: vec![0; ResourceCount],
            Air: vec![0; ResourceCount],
            Electrical: vec![0; ResourceCount],
            WireByOwner: HashMap::new(),
            ElectricalByOwner: HashMap::new(),
            CrossAirByWire,
        }
    }

    pub(in crate::Escape) fn IsCompatible(
        &self,
        Candidate: &AssignmentCandidate,
        Owner: usize,
    ) -> bool {
        let (Wire, Support, Air, Electrical) = Candidate.Claims.IndexSets();
        let StaticCompatible = Wire.iter().all(|Resource| {
            self.Support[*Resource] == 0
                && self.Air[*Resource] == 0
                && self.Electrical[*Resource]
                    == *self
                        .ElectricalByOwner
                        .get(&(*Resource, Owner))
                        .unwrap_or(&0)
        }) && Support
            .iter()
            .all(|Resource| self.Wire[*Resource] == 0 && self.Air[*Resource] == 0)
            && Air
                .iter()
                .all(|Resource| self.Wire[*Resource] == 0 && self.Support[*Resource] == 0)
            && Electrical.iter().all(|Resource| {
                self.Wire[*Resource] == *self.WireByOwner.get(&(*Resource, Owner)).unwrap_or(&0)
            });
        StaticCompatible
            && Wire.iter().all(|Resource| {
                self.CrossAirByWire[*Resource]
                    .iter()
                    .all(|(OtherWire, AirResource)| {
                        self.WireByOwner
                            .get(&(*OtherWire, Owner))
                            .copied()
                            .unwrap_or(0)
                            == 0
                            || (self.Wire[*AirResource] == 0
                                && Wire.binary_search(AirResource).is_err()
                                && self.Support[*AirResource] == 0
                                && Support.binary_search(AirResource).is_err())
                    })
            })
    }

    pub(in crate::Escape) fn Add(&mut self, Candidate: &AssignmentCandidate, Owner: usize) {
        let (Wire, Support, Air, Electrical) = Candidate.Claims.IndexSets();
        for Resource in Wire {
            for (OtherWire, AirResource) in &self.CrossAirByWire[*Resource] {
                let ExistingOtherWireCount = self
                    .WireByOwner
                    .get(&(*OtherWire, Owner))
                    .copied()
                    .unwrap_or(0);
                self.Air[*AirResource] += ExistingOtherWireCount;
            }
        }
        for Resource in Wire {
            self.Wire[*Resource] += 1;
            *self.WireByOwner.entry((*Resource, Owner)).or_default() += 1;
        }
        for Resource in Support {
            self.Support[*Resource] += 1;
        }
        for Resource in Air {
            self.Air[*Resource] += 1;
        }
        for Resource in Electrical {
            self.Electrical[*Resource] += 1;
            *self
                .ElectricalByOwner
                .entry((*Resource, Owner))
                .or_default() += 1;
        }
    }

    pub(in crate::Escape) fn Remove(&mut self, Candidate: &AssignmentCandidate, Owner: usize) {
        let (Wire, Support, Air, Electrical) = Candidate.Claims.IndexSets();
        for Resource in Wire {
            for (OtherWire, AirResource) in &self.CrossAirByWire[*Resource] {
                let ExistingOtherWireCount = self
                    .WireByOwner
                    .get(&(*OtherWire, Owner))
                    .copied()
                    .unwrap_or(0);
                let CandidateOtherWireCount = usize::from(Wire.binary_search(OtherWire).is_ok());
                self.Air[*AirResource] -=
                    ExistingOtherWireCount.saturating_sub(CandidateOtherWireCount);
            }
        }
        for Resource in Wire {
            self.Wire[*Resource] -= 1;
            let Key = (*Resource, Owner);
            let Value = self
                .WireByOwner
                .get_mut(&Key)
                .expect("selected wire owner occupancy");
            *Value -= 1;
            if *Value == 0 {
                self.WireByOwner.remove(&Key);
            }
        }
        for Resource in Support {
            self.Support[*Resource] -= 1;
        }
        for Resource in Air {
            self.Air[*Resource] -= 1;
        }
        for Resource in Electrical {
            self.Electrical[*Resource] -= 1;
            let Key = (*Resource, Owner);
            let Value = self
                .ElectricalByOwner
                .get_mut(&Key)
                .expect("selected electrical owner occupancy");
            *Value -= 1;
            if *Value == 0 {
                self.ElectricalByOwner.remove(&Key);
            }
        }
    }
}

#[derive(Clone)]
pub(in crate::Escape) struct LayeredCatalogSelectionState {
    pub(in crate::Escape) SelectedByVariable: BTreeMap<String, usize>,
    pub(in crate::Escape) SelectedOrder: Vec<(String, usize)>,
    pub(in crate::Escape) RequirementChoices: BTreeMap<String, String>,
    pub(in crate::Escape) Occupancy: LayeredCatalogClaimOccupancy,
}

impl LayeredCatalogSelectionState {
    pub(in crate::Escape) fn New(
        ResourceCount: usize,
        CrossAirByWire: Arc<Vec<Vec<(usize, usize)>>>,
    ) -> Self {
        Self {
            SelectedByVariable: BTreeMap::new(),
            SelectedOrder: Vec::new(),
            RequirementChoices: BTreeMap::new(),
            Occupancy: LayeredCatalogClaimOccupancy::New(ResourceCount, CrossAirByWire),
        }
    }
}

#[allow(dead_code)]
pub(in crate::Escape) struct LayeredCatalogSearchContext<'a> {
    pub(in crate::Escape) Groups: &'a BTreeMap<String, Vec<AssignmentCandidate>>,
    pub(in crate::Escape) CandidateIndexByIdByVariable: &'a HashMap<String, HashMap<String, usize>>,
    pub(in crate::Escape) PreferredCandidateIdByVariable: &'a HashMap<String, String>,
    pub(in crate::Escape) AccessChoicesByPortalRequirement:
        &'a HashMap<(String, String), Vec<(String, usize)>>,
    pub(in crate::Escape) AccessChoicesByStubRequirement:
        &'a HashMap<(String, String), Vec<(String, usize)>>,
    pub(in crate::Escape) OwnerIndexByName: &'a HashMap<String, usize>,
    pub(in crate::Escape) SharedExpansionCount: &'a std::sync::atomic::AtomicUsize,
    pub(in crate::Escape) MaximumExpansionCount: usize,
    pub(in crate::Escape) Deadline: &'a RuntimeDeadline,
    pub(in crate::Escape) ExpansionCount: usize,
    pub(in crate::Escape) MaximumSelectedCount: usize,
    pub(in crate::Escape) DeepestFailureDepth: usize,
    pub(in crate::Escape) DeepestFailureNet: Option<String>,
    pub(in crate::Escape) SearchVariant: usize,
    pub(in crate::Escape) LocalMaximumExpansionCount: Option<usize>,
    pub(in crate::Escape) LocalBudgetExhausted: bool,
    pub(in crate::Escape) BudgetExhausted: bool,
    pub(in crate::Escape) DeadlineExceeded: bool,
    pub(in crate::Escape) FailureNet: Option<String>,
}

pub(in crate::Escape) fn RecordLayeredCatalogFailure(
    Context: &mut LayeredCatalogSearchContext,
    State: &LayeredCatalogSelectionState,
    Variable: &str,
) {
    if State.SelectedByVariable.len() >= Context.DeepestFailureDepth {
        Context.DeepestFailureDepth = State.SelectedByVariable.len();
        Context.DeepestFailureNet = Some(Variable.to_string());
    }
    Context.FailureNet = Some(Variable.to_string());
}

pub(in crate::Escape) fn LayeredCatalogRotatedIndices(
    Count: usize,
    Identity: &str,
    SearchVariant: usize,
) -> std::vec::IntoIter<usize> {
    if Count == 0 {
        return Vec::new().into_iter();
    }
    if Count == 1 {
        return vec![0].into_iter();
    }
    let IdentityValue = Identity.bytes().fold(0usize, |Value, Byte| {
        Value.wrapping_mul(131).wrapping_add(Byte as usize)
    });
    let AlternativeCount = Count - 1;
    let AlternativeStart = IdentityValue.wrapping_add(SearchVariant) % AlternativeCount;
    let mut Result = Vec::with_capacity(Count);
    // Candidate zero is the exact pairwise warm-start value after the
    // deterministic domain reordering above.  Preserve that information while
    // still rotating the remaining alternatives between bounded branches.
    Result.push(0);
    Result.extend(
        (0..AlternativeCount).map(|Offset| 1 + (AlternativeStart + Offset) % AlternativeCount),
    );
    Result.into_iter()
}

pub(in crate::Escape) fn ConsumeLayeredCatalogExpansion(
    Context: &mut LayeredCatalogSearchContext,
    Variable: &str,
) -> bool {
    if Context.Deadline.Check() {
        Context.DeadlineExceeded = true;
        Context.FailureNet = Some(Variable.to_string());
        return false;
    }
    if Context
        .LocalMaximumExpansionCount
        .is_some_and(|Maximum| Context.ExpansionCount >= Maximum)
    {
        Context.LocalBudgetExhausted = true;
        Context.FailureNet = Some(Variable.to_string());
        return false;
    }
    if Context
        .SharedExpansionCount
        .fetch_update(
            std::sync::atomic::Ordering::SeqCst,
            std::sync::atomic::Ordering::SeqCst,
            |Value| (Value < Context.MaximumExpansionCount).then_some(Value + 1),
        )
        .is_err()
    {
        Context.BudgetExhausted = true;
        Context.FailureNet = Some(Variable.to_string());
        return false;
    }
    Context.ExpansionCount += 1;
    true
}

pub(in crate::Escape) fn RollbackLayeredCatalogSelection(
    Context: &LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    SelectedCheckpoint: usize,
    NewRequirementNames: &mut Vec<String>,
) {
    while State.SelectedOrder.len() > SelectedCheckpoint {
        let (Variable, CandidateIndex) = State
            .SelectedOrder
            .pop()
            .expect("selected catalog value beyond checkpoint");
        let Candidate = &Context.Groups[&Variable][CandidateIndex];
        let Owner = Context.OwnerIndexByName[&Candidate.OwnerSignal];
        State.Occupancy.Remove(Candidate, Owner);
        State.SelectedByVariable.remove(&Variable);
    }
    for Name in NewRequirementNames.drain(..).rev() {
        State.RequirementChoices.remove(&Name);
    }
}

pub(in crate::Escape) fn ApplyLayeredCatalogCandidate(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    Variable: &str,
    CandidateIndex: usize,
    NewRequirementNames: &mut Vec<String>,
) -> bool {
    if let Some(SelectedIndex) = State.SelectedByVariable.get(Variable) {
        return *SelectedIndex == CandidateIndex;
    }
    let Candidate = &Context.Groups[Variable][CandidateIndex];
    for (Name, Value) in Candidate.TemplateRequirements.iter() {
        if State
            .RequirementChoices
            .get(Name)
            .is_some_and(|SelectedValue| SelectedValue != Value)
        {
            return false;
        }
    }
    let Owner = Context.OwnerIndexByName[&Candidate.OwnerSignal];
    if !State.Occupancy.IsCompatible(Candidate, Owner) {
        return false;
    }
    for (Name, Value) in Candidate.TemplateRequirements.iter() {
        if !State.RequirementChoices.contains_key(Name) {
            State.RequirementChoices.insert(Name.clone(), Value.clone());
            NewRequirementNames.push(Name.clone());
        }
    }
    State
        .SelectedByVariable
        .insert(Variable.to_string(), CandidateIndex);
    State.Occupancy.Add(Candidate, Owner);
    State
        .SelectedOrder
        .push((Variable.to_string(), CandidateIndex));
    Context.MaximumSelectedCount = Context
        .MaximumSelectedCount
        .max(State.SelectedByVariable.len());
    true
}

pub(in crate::Escape) fn ApplyCompatibleWarmLayeredCatalogAccess(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    AccessVariables: &[String],
    WarmCandidateIdByVariable: &HashMap<String, String>,
    WarmRequirementNames: &mut Vec<String>,
) -> bool {
    for Variable in AccessVariables {
        if State.SelectedByVariable.contains_key(Variable) {
            continue;
        }
        let Some(PreferredCandidateId) = WarmCandidateIdByVariable.get(Variable) else {
            continue;
        };
        let Some(CandidateIndex) = Context.Groups[Variable]
            .iter()
            .position(|Candidate| Candidate.CandidateId == *PreferredCandidateId)
        else {
            continue;
        };
        let Candidate = &Context.Groups[Variable][CandidateIndex];
        let PortalContractIsFixed = Candidate.TemplateRequirements.iter().all(|(Name, Value)| {
            !Name.starts_with("access-portal:") || State.RequirementChoices.get(Name) == Some(Value)
        });
        if !PortalContractIsFixed {
            continue;
        }
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        if !ConsumeLayeredCatalogExpansion(Context, Variable) {
            return false;
        }
        if ApplyLayeredCatalogCandidate(
            Context,
            State,
            Variable,
            CandidateIndex,
            &mut NewRequirementNames,
        ) {
            WarmRequirementNames.extend(NewRequirementNames);
        } else {
            RollbackLayeredCatalogSelection(
                Context,
                State,
                SelectedCheckpoint,
                &mut NewRequirementNames,
            );
        }
    }
    true
}

pub(in crate::Escape) fn LayeredCatalogVariableIsEligible(
    Context: &LayeredCatalogSearchContext,
    State: &LayeredCatalogSelectionState,
    Variable: &str,
    Candidates: &[AssignmentCandidate],
) -> bool {
    if !Variable.starts_with("__access_terminal__:") {
        return true;
    }
    Candidates
        .first()
        .and_then(|Candidate| {
            Candidate
                .TemplateRequirements
                .iter()
                .find(|(Name, _Value)| Name.starts_with("access-portal:"))
        })
        .is_none_or(|(Name, _Value)| {
            State.RequirementChoices.contains_key(Name)
                || !Context.Groups.iter().any(|(OtherVariable, Values)| {
                    OtherVariable.starts_with("__route_guide__:")
                        && Values.iter().any(|Candidate| {
                            Candidate
                                .TemplateRequirements
                                .iter()
                                .any(|(RequirementName, _RequirementValue)| RequirementName == Name)
                        })
                })
        })
}

#[allow(dead_code)]
pub(in crate::Escape) fn SearchLayeredCatalogFactors(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
) -> bool {
    if Context.BudgetExhausted || Context.DeadlineExceeded {
        return false;
    }
    if State.SelectedByVariable.len() == Context.Groups.len() {
        return true;
    }
    let mut BestVariable = None::<String>;
    let mut BestCandidates = Vec::<usize>::new();
    for (Variable, Candidates) in Context.Groups {
        if State.SelectedByVariable.contains_key(Variable)
            || !LayeredCatalogVariableIsEligible(Context, State, Variable, Candidates)
        {
            continue;
        }
        let mut ViableCandidates = Vec::new();
        for CandidateIndex in 0..Candidates.len() {
            if Context.Deadline.Check() {
                Context.DeadlineExceeded = true;
                Context.FailureNet = Some(Variable.clone());
                return false;
            }
            let SelectedCheckpoint = State.SelectedOrder.len();
            let mut NewRequirementNames = Vec::new();
            let Viable = ApplyLayeredCatalogCandidate(
                Context,
                State,
                Variable,
                CandidateIndex,
                &mut NewRequirementNames,
            );
            RollbackLayeredCatalogSelection(
                Context,
                State,
                SelectedCheckpoint,
                &mut NewRequirementNames,
            );
            if Viable {
                ViableCandidates.push(CandidateIndex);
            }
        }
        if ViableCandidates.is_empty() {
            Context.FailureNet = Some(Variable.clone());
            return false;
        }
        if BestVariable.as_ref().is_none_or(|BestName| {
            (ViableCandidates.len(), Variable) < (BestCandidates.len(), BestName)
        }) {
            BestVariable = Some(Variable.clone());
            BestCandidates = ViableCandidates;
        }
    }
    let Some(Variable) = BestVariable else {
        Context.FailureNet = Context
            .Groups
            .keys()
            .find(|Variable| !State.SelectedByVariable.contains_key(*Variable))
            .cloned();
        return false;
    };
    let DiversifiedCandidateOrder =
        LayeredCatalogRotatedIndices(BestCandidates.len(), &Variable, Context.SearchVariant)
            .map(|ChoiceIndex| BestCandidates[ChoiceIndex])
            .collect::<Vec<_>>();
    let FutureVariables = Context
        .Groups
        .keys()
        .filter(|FutureVariable| {
            FutureVariable.as_str() != Variable
                && !State.SelectedByVariable.contains_key(*FutureVariable)
        })
        .cloned()
        .collect::<Vec<_>>();
    let mut RankedCandidateOrder = Vec::with_capacity(DiversifiedCandidateOrder.len());
    for (OrderIndex, CandidateIndex) in DiversifiedCandidateOrder.into_iter().enumerate() {
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        let Applied = ApplyLayeredCatalogCandidate(
            Context,
            State,
            &Variable,
            CandidateIndex,
            &mut NewRequirementNames,
        );
        let mut FutureDeadEndCount = 0usize;
        let mut FutureConflictCount = 0usize;
        if Applied {
            for FutureVariable in &FutureVariables {
                let FutureCandidates = &Context.Groups[FutureVariable];
                if !LayeredCatalogVariableIsEligible(
                    Context,
                    State,
                    FutureVariable,
                    FutureCandidates,
                ) {
                    continue;
                }
                let mut CompatibleFutureCount = 0usize;
                for FutureCandidateIndex in 0..FutureCandidates.len() {
                    if Context.Deadline.Check() {
                        Context.DeadlineExceeded = true;
                        Context.FailureNet = Some(FutureVariable.clone());
                        break;
                    }
                    let FutureCheckpoint = State.SelectedOrder.len();
                    let mut FutureRequirementNames = Vec::new();
                    let FutureSupported = ApplyLayeredCatalogCandidate(
                        Context,
                        State,
                        FutureVariable,
                        FutureCandidateIndex,
                        &mut FutureRequirementNames,
                    );
                    RollbackLayeredCatalogSelection(
                        Context,
                        State,
                        FutureCheckpoint,
                        &mut FutureRequirementNames,
                    );
                    CompatibleFutureCount += usize::from(FutureSupported);
                }
                if Context.DeadlineExceeded {
                    break;
                }
                FutureDeadEndCount += usize::from(CompatibleFutureCount == 0);
                FutureConflictCount = FutureConflictCount
                    .saturating_add(FutureCandidates.len().saturating_sub(CompatibleFutureCount));
            }
        }
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Context.DeadlineExceeded {
            return false;
        }
        RankedCandidateOrder.push((
            FutureDeadEndCount,
            FutureConflictCount,
            OrderIndex,
            CandidateIndex,
        ));
    }
    RankedCandidateOrder.sort_unstable();
    let CandidateOrder = RankedCandidateOrder
        .into_iter()
        .map(|(_DeadEnds, _Conflicts, _OrderIndex, CandidateIndex)| CandidateIndex)
        .collect::<Vec<_>>();
    for CandidateIndex in CandidateOrder {
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        if ConsumeLayeredCatalogExpansion(Context, &Variable)
            && ApplyLayeredCatalogCandidate(
                Context,
                State,
                &Variable,
                CandidateIndex,
                &mut NewRequirementNames,
            )
            && SearchLayeredCatalogFactors(Context, State)
        {
            return true;
        }
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Context.BudgetExhausted || Context.DeadlineExceeded || Context.LocalBudgetExhausted {
            return false;
        }
    }
    Context.FailureNet = Some(Variable);
    false
}

pub(in crate::Escape) fn SearchLayeredCatalogPortalChoices(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    GuideVariable: &str,
    GuideCandidateIndex: usize,
    PortalRequirements: &[(String, String)],
    RequirementIndex: usize,
    RemainingGuideVariables: &[String],
    AccessVariables: &[String],
) -> bool {
    if Context.BudgetExhausted || Context.DeadlineExceeded {
        return false;
    }
    let Some((Name, Value)) = PortalRequirements.get(RequirementIndex) else {
        let SignalNames = Context.Groups.keys().cloned().collect::<Vec<_>>();
        let SignalIndexByName = SignalNames
            .iter()
            .enumerate()
            .map(|(Index, Variable)| (Variable.as_str(), Index))
            .collect::<HashMap<_, _>>();
        let mut Selection = vec![None; SignalNames.len()];
        let Some(GuideSignalIndex) = SignalIndexByName.get(GuideVariable).copied() else {
            Context.FailureNet = Some(GuideVariable.to_string());
            return false;
        };
        Selection[GuideSignalIndex] = Some(GuideCandidateIndex);
        let Some(Constraint) = Context.Groups[GuideVariable][GuideCandidateIndex]
            .PoweredAccessConstraint
            .as_ref()
        else {
            Context.FailureNet = Some(GuideVariable.to_string());
            return false;
        };
        for AccessVariable in Constraint.TerminalVariables.iter() {
            let Some(AccessSignalIndex) = SignalIndexByName.get(AccessVariable.as_str()).copied()
            else {
                Context.FailureNet = Some(AccessVariable.clone());
                return false;
            };
            let Some(AccessCandidateIndex) = State.SelectedByVariable.get(AccessVariable).copied()
            else {
                Context.FailureNet = Some(AccessVariable.clone());
                return false;
            };
            Selection[AccessSignalIndex] = Some(AccessCandidateIndex);
        }
        let mut PoweredFailureNet = None;
        match SelectionHasPoweredAccessWitnessExact(
            Context.Groups,
            &SignalNames,
            &Selection,
            Context.Deadline,
            &mut PoweredFailureNet,
        ) {
            Some(true) => {}
            Some(false) => {
                Context.FailureNet = PoweredFailureNet.or_else(|| Some(GuideVariable.to_string()));
                return false;
            }
            None => {
                Context.DeadlineExceeded = true;
                Context.FailureNet = PoweredFailureNet.or_else(|| Some(GuideVariable.to_string()));
                return false;
            }
        }
        return SearchLayeredCatalogGuidesByPortal(
            Context,
            State,
            RemainingGuideVariables,
            AccessVariables,
        );
    };
    let Some(Choices) = Context
        .AccessChoicesByPortalRequirement
        .get(&(Name.clone(), Value.clone()))
    else {
        Context.FailureNet = Some(Name.clone());
        return false;
    };
    let Some(AccessVariable) = Choices.first().map(|(Variable, _Index)| Variable) else {
        Context.FailureNet = Some(Name.clone());
        return false;
    };
    if let Some(SelectedCandidateIndex) = State.SelectedByVariable.get(AccessVariable) {
        let SelectedCandidate = &Context.Groups[AccessVariable][*SelectedCandidateIndex];
        if !SelectedCandidate
            .TemplateRequirements
            .iter()
            .any(|(SelectedName, SelectedValue)| SelectedName == Name && SelectedValue == Value)
        {
            Context.FailureNet = Some(AccessVariable.clone());
            return false;
        }
        return SearchLayeredCatalogPortalChoices(
            Context,
            State,
            GuideVariable,
            GuideCandidateIndex,
            PortalRequirements,
            RequirementIndex + 1,
            RemainingGuideVariables,
            AccessVariables,
        );
    }
    let mut ChoiceOrder = LayeredCatalogRotatedIndices(Choices.len(), Name, Context.SearchVariant)
        .collect::<Vec<_>>();
    let WitnessRequirementName = Name
        .strip_prefix("access-portal:")
        .map(|LogicalKey| format!("access-witness:{LogicalKey}"));
    let PreferredCandidateId = WitnessRequirementName
        .as_ref()
        .and_then(|WitnessName| {
            Context.Groups[GuideVariable][GuideCandidateIndex]
                .TemplateRequirements
                .iter()
                .find(|(RequirementName, _Value)| RequirementName == WitnessName)
                .map(|(_Name, Value)| Value)
        })
        .or_else(|| Context.PreferredCandidateIdByVariable.get(AccessVariable));
    if let Some(PreferredChoicePosition) = PreferredCandidateId.and_then(|PreferredId| {
        ChoiceOrder.iter().position(|ChoiceIndex| {
            let (ChoiceVariable, CandidateIndex) = &Choices[*ChoiceIndex];
            Context.Groups[ChoiceVariable][*CandidateIndex].CandidateId == *PreferredId
        })
    }) {
        let PreferredChoice = ChoiceOrder.remove(PreferredChoicePosition);
        ChoiceOrder.insert(0, PreferredChoice);
    }
    for ChoiceIndex in ChoiceOrder {
        let (ChoiceVariable, CandidateIndex) = &Choices[ChoiceIndex];
        if ChoiceVariable != AccessVariable {
            Context.FailureNet = Some(Name.clone());
            return false;
        }
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        if ConsumeLayeredCatalogExpansion(Context, AccessVariable)
            && ApplyLayeredCatalogCandidate(
                Context,
                State,
                AccessVariable,
                *CandidateIndex,
                &mut NewRequirementNames,
            )
            && SearchLayeredCatalogPortalChoices(
                Context,
                State,
                GuideVariable,
                GuideCandidateIndex,
                PortalRequirements,
                RequirementIndex + 1,
                RemainingGuideVariables,
                AccessVariables,
            )
        {
            return true;
        }
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Context.BudgetExhausted || Context.DeadlineExceeded || Context.LocalBudgetExhausted {
            return false;
        }
        if Context
            .FailureNet
            .as_deref()
            .is_some_and(|FailureVariable| {
                FailureVariable.starts_with("__route_guide__:")
                    && FailureVariable != GuideVariable
                    && !RemainingGuideVariables
                        .iter()
                        .any(|Variable| Variable == FailureVariable)
            })
        {
            // A complete witness check found that this repair disconnects a
            // guide which is still fixed to the indexed warm witness.  Bubble
            // that exact guide to the repair frontier immediately instead of
            // exhaustively varying the current guide against a value that is
            // already known to participate in the failure.
            return false;
        }
    }
    RecordLayeredCatalogFailure(Context, State, AccessVariable);
    false
}

#[allow(dead_code)]
pub(in crate::Escape) fn LayeredCatalogPortalChoicesHaveSupport(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    PortalRequirements: &[(String, String)],
    RequirementIndex: usize,
) -> bool {
    let RemainingRequirements = &PortalRequirements[RequirementIndex..];
    if RemainingRequirements.is_empty() {
        return true;
    }
    let mut SelectedRequirement = None::<(usize, String, Vec<(String, usize)>)>;
    for (RelativeIndex, (Name, Value)) in RemainingRequirements.iter().enumerate() {
        let Choices = Context
            .AccessChoicesByPortalRequirement
            .get(&(Name.clone(), Value.clone()))
            .cloned()
            .unwrap_or_default();
        let Some(AccessVariable) = Choices.first().map(|(Variable, _Index)| Variable.clone())
        else {
            return false;
        };
        if let Some(SelectedCandidateIndex) = State.SelectedByVariable.get(&AccessVariable) {
            if !Context.Groups[&AccessVariable][*SelectedCandidateIndex]
                .TemplateRequirements
                .iter()
                .any(|(SelectedName, SelectedValue)| SelectedName == Name && SelectedValue == Value)
            {
                return false;
            }
            let mut NextRequirements = RemainingRequirements.to_vec();
            NextRequirements.remove(RelativeIndex);
            return LayeredCatalogPortalChoicesHaveSupport(Context, State, &NextRequirements, 0);
        }
        let mut ViableChoices = Vec::new();
        for (ChoiceVariable, CandidateIndex) in Choices {
            if ChoiceVariable != AccessVariable {
                return false;
            }
            let SelectedCheckpoint = State.SelectedOrder.len();
            let mut NewRequirementNames = Vec::new();
            let Viable = ApplyLayeredCatalogCandidate(
                Context,
                State,
                &AccessVariable,
                CandidateIndex,
                &mut NewRequirementNames,
            );
            RollbackLayeredCatalogSelection(
                Context,
                State,
                SelectedCheckpoint,
                &mut NewRequirementNames,
            );
            if Viable {
                ViableChoices.push((AccessVariable.clone(), CandidateIndex));
            }
        }
        if ViableChoices.is_empty() {
            return false;
        }
        if SelectedRequirement
            .as_ref()
            .is_none_or(|(_BestIndex, BestVariable, BestChoices)| {
                (ViableChoices.len(), &AccessVariable) < (BestChoices.len(), BestVariable)
            })
        {
            SelectedRequirement = Some((RelativeIndex, AccessVariable, ViableChoices));
        }
    }
    let Some((RelativeIndex, AccessVariable, ViableChoices)) = SelectedRequirement else {
        return true;
    };
    let mut NextRequirements = RemainingRequirements.to_vec();
    NextRequirements.remove(RelativeIndex);
    let ChoiceOrder =
        LayeredCatalogRotatedIndices(ViableChoices.len(), &AccessVariable, Context.SearchVariant)
            .collect::<Vec<_>>();
    for ChoiceIndex in ChoiceOrder {
        let (_ChoiceVariable, CandidateIndex) = &ViableChoices[ChoiceIndex];
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        let Supported =
            ApplyLayeredCatalogCandidate(
                Context,
                State,
                &AccessVariable,
                *CandidateIndex,
                &mut NewRequirementNames,
            ) && LayeredCatalogPortalChoicesHaveSupport(Context, State, &NextRequirements, 0);
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Supported {
            return true;
        }
    }
    false
}

#[allow(dead_code)]
pub(in crate::Escape) fn ApplyLayeredCatalogExactStubRequirements(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    Requirements: &[(String, String)],
    CountExpansions: bool,
    NewRequirementNames: &mut Vec<String>,
) -> bool {
    for Requirement in Requirements {
        let Some(Choices) = Context.AccessChoicesByStubRequirement.get(Requirement) else {
            Context.FailureNet = Some(Requirement.0.clone());
            return false;
        };
        if Choices.len() != 1 {
            Context.FailureNet = Some(Requirement.0.clone());
            return false;
        }
        let (Variable, CandidateIndex) = &Choices[0];
        if State.SelectedByVariable.get(Variable) == Some(CandidateIndex) {
            continue;
        }
        if (CountExpansions && !ConsumeLayeredCatalogExpansion(Context, Variable))
            || !ApplyLayeredCatalogCandidate(
                Context,
                State,
                Variable,
                *CandidateIndex,
                NewRequirementNames,
            )
        {
            Context.FailureNet = Some(Variable.clone());
            return false;
        }
    }
    true
}

pub(in crate::Escape) fn ApplyLayeredCatalogCertifiedAccessTuple(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    CandidateTuple: &[(String, String)],
    CountExpansions: bool,
    NewRequirementNames: &mut Vec<String>,
) -> bool {
    for (Variable, CandidateId) in CandidateTuple {
        let Some(CandidateIndex) = Context
            .CandidateIndexByIdByVariable
            .get(Variable)
            .and_then(|Indices| Indices.get(CandidateId))
            .copied()
        else {
            Context.FailureNet = Some(Variable.clone());
            return false;
        };
        if State.SelectedByVariable.get(Variable) == Some(&CandidateIndex) {
            continue;
        }
        if (CountExpansions && !ConsumeLayeredCatalogExpansion(Context, Variable))
            || !ApplyLayeredCatalogCandidate(
                Context,
                State,
                Variable,
                CandidateIndex,
                NewRequirementNames,
            )
        {
            Context.FailureNet = Some(Variable.clone());
            return false;
        }
    }
    true
}

#[allow(dead_code)]
pub(in crate::Escape) fn LayeredCatalogSelectedGuideUsesCertifiedTuple(
    Context: &LayeredCatalogSearchContext,
    State: &LayeredCatalogSelectionState,
    Variable: &str,
) -> bool {
    let Some(CandidateIndex) = State.SelectedByVariable.get(Variable).copied() else {
        return false;
    };
    let Some(Constraint) = Context.Groups[Variable][CandidateIndex]
        .PoweredAccessConstraint
        .as_ref()
    else {
        return true;
    };
    Constraint
        .PreferredAccessCandidateTuples
        .iter()
        .any(|CandidateTuple| {
            CandidateTuple.iter().all(|(AccessVariable, CandidateId)| {
                State
                    .SelectedByVariable
                    .get(AccessVariable)
                    .is_some_and(|AccessCandidateIndex| {
                        Context.Groups[AccessVariable][*AccessCandidateIndex].CandidateId
                            == *CandidateId
                    })
            })
        })
}

pub(in crate::Escape) fn LayeredCatalogSelectedGuideHasPoweredWitness(
    Context: &LayeredCatalogSearchContext,
    State: &LayeredCatalogSelectionState,
    Variable: &str,
) -> Option<bool> {
    let GuideCandidateIndex = State.SelectedByVariable.get(Variable).copied()?;
    let Constraint = Context.Groups[Variable][GuideCandidateIndex]
        .PoweredAccessConstraint
        .as_ref()?;
    let SignalNames = Context.Groups.keys().cloned().collect::<Vec<_>>();
    let SignalIndexByName = SignalNames
        .iter()
        .enumerate()
        .map(|(Index, Signal)| (Signal.as_str(), Index))
        .collect::<HashMap<_, _>>();
    let mut Selection = vec![None; SignalNames.len()];
    Selection[*SignalIndexByName.get(Variable)?] = Some(GuideCandidateIndex);
    for AccessVariable in Constraint.TerminalVariables.iter() {
        let AccessSignalIndex = *SignalIndexByName.get(AccessVariable.as_str())?;
        Selection[AccessSignalIndex] = State.SelectedByVariable.get(AccessVariable).copied();
        Selection[AccessSignalIndex]?;
    }
    let mut FailureNet = None;
    SelectionHasPoweredAccessWitnessExact(
        Context.Groups,
        &SignalNames,
        &Selection,
        Context.Deadline,
        &mut FailureNet,
    )
}

pub(in crate::Escape) fn SearchLayeredCatalogSelectedGuideTuples(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    GuideVariables: &[String],
    AccessVariables: &[String],
) -> bool {
    if Context.BudgetExhausted || Context.DeadlineExceeded || Context.LocalBudgetExhausted {
        return false;
    }
    if GuideVariables.is_empty() {
        if !SearchLayeredCatalogAccessByPortal(Context, State, AccessVariables) {
            return false;
        }
        return State
            .SelectedByVariable
            .keys()
            .filter(|Variable| Variable.starts_with("__route_guide__:"))
            .all(|Variable| {
                LayeredCatalogSelectedGuideHasPoweredWitness(Context, State, Variable) == Some(true)
            });
    }

    let mut BestVariableIndex = 0usize;
    let mut BestVariable = None::<String>;
    let mut BestTupleIndices = Vec::<usize>::new();
    for (VariableIndex, Variable) in GuideVariables.iter().enumerate() {
        if Context.Deadline.Check() {
            Context.DeadlineExceeded = true;
            Context.FailureNet = Some(Variable.clone());
            return false;
        }
        let Some(GuideCandidateIndex) = State.SelectedByVariable.get(Variable).copied() else {
            Context.FailureNet = Some(Variable.clone());
            return false;
        };
        let Some(Constraint) = Context.Groups[Variable][GuideCandidateIndex]
            .PoweredAccessConstraint
            .as_ref()
        else {
            Context.FailureNet = Some(Variable.clone());
            return false;
        };
        let mut ViableTupleIndices = Vec::new();
        for (TupleIndex, CandidateTuple) in
            Constraint.PreferredAccessCandidateTuples.iter().enumerate()
        {
            let SelectedCheckpoint = State.SelectedOrder.len();
            let mut NewRequirementNames = Vec::new();
            let PreviousFailureNet = Context.FailureNet.clone();
            let Viable = ApplyLayeredCatalogCertifiedAccessTuple(
                Context,
                State,
                CandidateTuple,
                false,
                &mut NewRequirementNames,
            );
            RollbackLayeredCatalogSelection(
                Context,
                State,
                SelectedCheckpoint,
                &mut NewRequirementNames,
            );
            Context.FailureNet = PreviousFailureNet;
            if Viable {
                ViableTupleIndices.push(TupleIndex);
            }
        }
        if ViableTupleIndices.is_empty() {
            RecordLayeredCatalogFailure(Context, State, Variable);
            return false;
        }
        ViableTupleIndices.sort_by_key(|TupleIndex| {
            Constraint.PreferredAccessCandidateTuples[*TupleIndex]
                .iter()
                .filter(|(AccessVariable, CandidateId)| {
                    Context
                        .PreferredCandidateIdByVariable
                        .get(AccessVariable)
                        .is_some_and(|PreferredId| PreferredId != CandidateId)
                })
                .count()
        });
        if BestVariable.as_ref().is_none_or(|BestName| {
            (ViableTupleIndices.len(), Variable) < (BestTupleIndices.len(), BestName)
        }) {
            BestVariableIndex = VariableIndex;
            BestVariable = Some(Variable.clone());
            BestTupleIndices = ViableTupleIndices;
        }
    }

    let Variable = BestVariable.expect("selected guide tuple search has a best variable");
    let GuideCandidateIndex = State.SelectedByVariable[&Variable];
    let CertifiedTuples = Arc::clone(
        &Context.Groups[&Variable][GuideCandidateIndex]
            .PoweredAccessConstraint
            .as_ref()
            .expect("selected guide tuple search owns an access constraint")
            .PreferredAccessCandidateTuples,
    );
    let mut RemainingGuideVariables = GuideVariables.to_vec();
    RemainingGuideVariables.remove(BestVariableIndex);
    for TupleIndex in BestTupleIndices {
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        if ApplyLayeredCatalogCertifiedAccessTuple(
            Context,
            State,
            &CertifiedTuples[TupleIndex],
            true,
            &mut NewRequirementNames,
        ) && SearchLayeredCatalogSelectedGuideTuples(
            Context,
            State,
            &RemainingGuideVariables,
            AccessVariables,
        ) {
            return true;
        }
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Context.BudgetExhausted || Context.DeadlineExceeded || Context.LocalBudgetExhausted {
            return false;
        }
    }
    RecordLayeredCatalogFailure(Context, State, &Variable);
    false
}

pub(in crate::Escape) fn LayeredCatalogAccessVariablesForGuides(
    Context: &LayeredCatalogSearchContext,
    GuideVariables: &BTreeSet<String>,
) -> BTreeSet<String> {
    GuideVariables
        .iter()
        .flat_map(|Variable| Context.Groups[Variable].iter())
        .filter_map(|Candidate| Candidate.PoweredAccessConstraint.as_ref())
        .flat_map(|Constraint| Constraint.TerminalVariables.iter().cloned())
        .collect()
}

pub(in crate::Escape) fn TryLayeredCatalogGuideCandidate(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    Variable: &str,
    CandidateIndex: usize,
    RemainingGuideVariables: &[String],
    AccessVariables: &[String],
) -> bool {
    let CertifiedTuples = Context.Groups[Variable][CandidateIndex]
        .PoweredAccessConstraint
        .as_ref()
        .map(|Constraint| Arc::clone(&Constraint.PreferredAccessCandidateTuples))
        .unwrap_or_default();
    let mut CertifiedTupleOrder = (0..CertifiedTuples.len()).collect::<Vec<_>>();
    CertifiedTupleOrder.sort_by_key(|TupleIndex| {
        (
            CertifiedTuples[*TupleIndex]
                .iter()
                .filter(|(AccessVariable, CandidateId)| {
                    Context
                        .PreferredCandidateIdByVariable
                        .get(AccessVariable)
                        .is_some_and(|PreferredId| PreferredId != CandidateId)
                })
                .count(),
            *TupleIndex,
        )
    });
    for TupleIndex in CertifiedTupleOrder {
        let CandidateTuple = &CertifiedTuples[TupleIndex];
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        if ConsumeLayeredCatalogExpansion(Context, Variable)
            && ApplyLayeredCatalogCandidate(
                Context,
                State,
                Variable,
                CandidateIndex,
                &mut NewRequirementNames,
            )
            && ApplyLayeredCatalogCertifiedAccessTuple(
                Context,
                State,
                CandidateTuple,
                true,
                &mut NewRequirementNames,
            )
            && SearchLayeredCatalogGuidesByPortal(
                Context,
                State,
                RemainingGuideVariables,
                AccessVariables,
            )
        {
            return true;
        }
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Context.BudgetExhausted || Context.DeadlineExceeded || Context.LocalBudgetExhausted {
            return false;
        }
    }
    let SelectedCheckpoint = State.SelectedOrder.len();
    let mut NewRequirementNames = Vec::new();
    let mut PortalRequirements = Context.Groups[Variable][CandidateIndex]
        .TemplateRequirements
        .iter()
        .filter(|(Name, _Value)| Name.starts_with("access-portal:"))
        .cloned()
        .collect::<Vec<_>>();
    PortalRequirements.sort_by_key(|Requirement| {
        (
            Context
                .AccessChoicesByPortalRequirement
                .get(Requirement)
                .map(Vec::len)
                .unwrap_or(0),
            Requirement.clone(),
        )
    });
    if ConsumeLayeredCatalogExpansion(Context, Variable)
        && ApplyLayeredCatalogCandidate(
            Context,
            State,
            Variable,
            CandidateIndex,
            &mut NewRequirementNames,
        )
        && SearchLayeredCatalogPortalChoices(
            Context,
            State,
            Variable,
            CandidateIndex,
            &PortalRequirements,
            0,
            RemainingGuideVariables,
            AccessVariables,
        )
    {
        return true;
    }
    RollbackLayeredCatalogSelection(Context, State, SelectedCheckpoint, &mut NewRequirementNames);
    false
}

/// Search the exact powered witness basis without materializing unioned guide
/// candidates.  Every value is a reference to one guide candidate plus one of
/// its certified access tuples; applying those referenced candidates to the
/// shared occupancy is equivalent to the expanded bundle domain, but keeps the
/// catalog factorized.  The retained tuples are a sufficient witness basis,
/// not an exhaustive relation, so a failed search is only a seed failure and
/// must fall through to the complete portal search.
#[allow(dead_code)]
pub(in crate::Escape) fn SearchLayeredCatalogCertifiedBundleSeed(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    GuideVariables: &[String],
    AccessVariables: &[String],
) -> bool {
    if Context.BudgetExhausted || Context.DeadlineExceeded || Context.LocalBudgetExhausted {
        return false;
    }
    if GuideVariables.is_empty() {
        if !SearchLayeredCatalogAccessByPortal(Context, State, AccessVariables) {
            return false;
        }
        return State
            .SelectedByVariable
            .keys()
            .filter(|Variable| Variable.starts_with("__route_guide__:"))
            .all(|Variable| {
                LayeredCatalogSelectedGuideHasPoweredWitness(Context, State, Variable) == Some(true)
            });
    }

    let mut BestVariableIndex = 0usize;
    let mut BestVariable = None::<String>;
    let mut BestBundles = Vec::<(usize, usize, usize)>::new();
    for (VariableIndex, Variable) in GuideVariables.iter().take(1).enumerate() {
        if Context.Deadline.Check() {
            Context.DeadlineExceeded = true;
            Context.FailureNet = Some(Variable.clone());
            return false;
        }
        let mut Bundles = Vec::<(usize, usize, usize)>::new();
        let mut OrderIndex = 0usize;
        for CandidateIndex in LayeredCatalogRotatedIndices(
            Context.Groups[Variable].len(),
            Variable,
            Context.SearchVariant,
        ) {
            let Candidate = &Context.Groups[Variable][CandidateIndex];
            let Some(Constraint) = Candidate.PoweredAccessConstraint.as_ref() else {
                continue;
            };
            for (TupleIndex, CandidateTuple) in
                Constraint.PreferredAccessCandidateTuples.iter().enumerate()
            {
                let SelectedCheckpoint = State.SelectedOrder.len();
                let mut NewRequirementNames = Vec::new();
                let PreviousFailureNet = Context.FailureNet.clone();
                let Viable = ApplyLayeredCatalogCandidate(
                    Context,
                    State,
                    Variable,
                    CandidateIndex,
                    &mut NewRequirementNames,
                ) && ApplyLayeredCatalogCertifiedAccessTuple(
                    Context,
                    State,
                    CandidateTuple,
                    false,
                    &mut NewRequirementNames,
                );
                RollbackLayeredCatalogSelection(
                    Context,
                    State,
                    SelectedCheckpoint,
                    &mut NewRequirementNames,
                );
                Context.FailureNet = PreviousFailureNet;
                if Viable {
                    Bundles.push((CandidateIndex, TupleIndex, OrderIndex));
                }
                OrderIndex = OrderIndex.saturating_add(1);
            }
        }
        if Bundles.is_empty() {
            RecordLayeredCatalogFailure(Context, State, Variable);
            return false;
        }
        Bundles.sort_by_key(|(CandidateIndex, TupleIndex, OriginalOrder)| {
            let Candidate = &Context.Groups[Variable][*CandidateIndex];
            let CandidateTuple = &Candidate
                .PoweredAccessConstraint
                .as_ref()
                .expect("certified bundle seed owns an access constraint")
                .PreferredAccessCandidateTuples[*TupleIndex];
            let WarmMismatchCount = usize::from(
                Context
                    .PreferredCandidateIdByVariable
                    .get(Variable)
                    .is_some_and(|CandidateId| CandidateId != &Candidate.CandidateId),
            ) + CandidateTuple
                .iter()
                .filter(|(AccessVariable, CandidateId)| {
                    Context
                        .PreferredCandidateIdByVariable
                        .get(AccessVariable)
                        .is_some_and(|PreferredId| PreferredId != CandidateId)
                })
                .count();
            (WarmMismatchCount, *OriginalOrder)
        });
        if BestVariable
            .as_ref()
            .is_none_or(|BestName| (Bundles.len(), Variable) < (BestBundles.len(), BestName))
        {
            BestVariableIndex = VariableIndex;
            BestVariable = Some(Variable.clone());
            BestBundles = Bundles;
        }
    }

    let Variable = BestVariable.expect("nonempty guide seed has a best variable");
    let mut RemainingGuideVariables = GuideVariables.to_vec();
    RemainingGuideVariables.remove(BestVariableIndex);
    for (CandidateIndex, TupleIndex, _OriginalOrder) in BestBundles {
        let CandidateTuple = Arc::clone(
            &Context.Groups[&Variable][CandidateIndex]
                .PoweredAccessConstraint
                .as_ref()
                .expect("certified bundle seed owns an access constraint")
                .PreferredAccessCandidateTuples,
        );
        let CandidateTuple = &CandidateTuple[TupleIndex];
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        if ConsumeLayeredCatalogExpansion(Context, &Variable)
            && ApplyLayeredCatalogCandidate(
                Context,
                State,
                &Variable,
                CandidateIndex,
                &mut NewRequirementNames,
            )
            && ApplyLayeredCatalogCertifiedAccessTuple(
                Context,
                State,
                CandidateTuple,
                true,
                &mut NewRequirementNames,
            )
            && SearchLayeredCatalogCertifiedBundleSeed(
                Context,
                State,
                &RemainingGuideVariables,
                AccessVariables,
            )
        {
            return true;
        }
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Context.BudgetExhausted || Context.DeadlineExceeded || Context.LocalBudgetExhausted {
            return false;
        }
    }
    RecordLayeredCatalogFailure(Context, State, &Variable);
    false
}

pub(in crate::Escape) fn SearchLayeredCatalogGuidesByPortal(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    GuideVariables: &[String],
    AccessVariables: &[String],
) -> bool {
    if Context.BudgetExhausted || Context.DeadlineExceeded {
        return false;
    }
    if GuideVariables.is_empty() {
        if !SearchLayeredCatalogAccessByPortal(Context, State, AccessVariables) {
            return false;
        }
        return State
            .SelectedByVariable
            .keys()
            .filter(|Variable| Variable.starts_with("__route_guide__:"))
            .all(|Variable| {
                LayeredCatalogSelectedGuideHasPoweredWitness(Context, State, Variable) == Some(true)
            });
    }
    let mut BestVariableIndex = 0usize;
    let mut BestVariable = None::<String>;
    let mut BestViableCandidates = Vec::new();
    // The domains are already deterministically ordered by finite candidate
    // count.  Probing every remaining guide and every certified tuple at each
    // node repeats almost the entire RCA catalog merely to recompute MRV.
    // Selecting the first ordered guide preserves the complete DFS domain and
    // the shared work/deadline bounds while making each tuple probe occur only
    // when that guide is actually branched.
    for (VariableIndex, Variable) in GuideVariables.iter().take(1).enumerate() {
        let mut ViableCandidates = Vec::new();
        let mut CandidateOrder = LayeredCatalogRotatedIndices(
            Context.Groups[Variable].len(),
            Variable,
            Context.SearchVariant,
        )
        .collect::<Vec<_>>();
        if let Some(PreferredCandidateIndex) = Context
            .PreferredCandidateIdByVariable
            .get(Variable)
            .and_then(|CandidateId| Context.CandidateIndexByIdByVariable[Variable].get(CandidateId))
            .copied()
        {
            if let Some(PreferredPosition) = CandidateOrder
                .iter()
                .position(|CandidateIndex| *CandidateIndex == PreferredCandidateIndex)
            {
                CandidateOrder[..=PreferredPosition].rotate_right(1);
            }
        }
        for CandidateIndex in CandidateOrder {
            if Context.Deadline.Check() {
                Context.DeadlineExceeded = true;
                Context.FailureNet = Some(Variable.clone());
                return false;
            }
            let SelectedCheckpoint = State.SelectedOrder.len();
            let mut NewRequirementNames = Vec::new();
            let Supported = ApplyLayeredCatalogCandidate(
                Context,
                State,
                Variable,
                CandidateIndex,
                &mut NewRequirementNames,
            );
            RollbackLayeredCatalogSelection(
                Context,
                State,
                SelectedCheckpoint,
                &mut NewRequirementNames,
            );
            if Supported {
                ViableCandidates.push(CandidateIndex);
            }
        }
        if ViableCandidates.is_empty() {
            RecordLayeredCatalogFailure(Context, State, Variable);
            return false;
        }
        if BestVariable.as_ref().is_none_or(|BestName| {
            (ViableCandidates.len(), Variable) < (BestViableCandidates.len(), BestName)
        }) {
            BestVariableIndex = VariableIndex;
            BestVariable = Some(Variable.clone());
            BestViableCandidates = ViableCandidates;
        }
    }
    let Variable = BestVariable.expect("nonempty guide frontier has a best variable");
    let mut RemainingGuideVariables = GuideVariables.to_vec();
    RemainingGuideVariables.remove(BestVariableIndex);
    let RankedCandidateIndices = BestViableCandidates;
    let PreviousLocalMaximumExpansionCount = Context.LocalMaximumExpansionCount;
    let PreviousLocalBudgetExhausted = Context.LocalBudgetExhausted;
    let RemainingExpansionCount = PreviousLocalMaximumExpansionCount
        .unwrap_or(Context.MaximumExpansionCount)
        .saturating_sub(Context.ExpansionCount);
    let ShallowBranchExpansionAllowance = RemainingExpansionCount
        .checked_div(RankedCandidateIndices.len().saturating_add(1))
        .unwrap_or(0)
        .clamp(64, 1_024);
    let mut DeferredCandidateIndices = Vec::new();
    for CandidateIndex in &RankedCandidateIndices {
        Context.LocalMaximumExpansionCount = Some(
            PreviousLocalMaximumExpansionCount
                .unwrap_or(Context.MaximumExpansionCount)
                .min(
                    Context
                        .ExpansionCount
                        .saturating_add(ShallowBranchExpansionAllowance),
                ),
        );
        Context.LocalBudgetExhausted = false;
        if TryLayeredCatalogGuideCandidate(
            Context,
            State,
            &Variable,
            *CandidateIndex,
            &RemainingGuideVariables,
            AccessVariables,
        ) {
            Context.LocalMaximumExpansionCount = PreviousLocalMaximumExpansionCount;
            Context.LocalBudgetExhausted = PreviousLocalBudgetExhausted;
            return true;
        }
        let BranchBudgetExhausted = Context.LocalBudgetExhausted;
        Context.LocalMaximumExpansionCount = PreviousLocalMaximumExpansionCount;
        Context.LocalBudgetExhausted = PreviousLocalBudgetExhausted;
        if BranchBudgetExhausted {
            DeferredCandidateIndices.push(*CandidateIndex);
        }
        if Context.BudgetExhausted || Context.DeadlineExceeded {
            return false;
        }
    }
    for CandidateIndex in DeferredCandidateIndices {
        if TryLayeredCatalogGuideCandidate(
            Context,
            State,
            &Variable,
            CandidateIndex,
            &RemainingGuideVariables,
            AccessVariables,
        ) {
            return true;
        }
        if Context.BudgetExhausted || Context.DeadlineExceeded {
            return false;
        }
        if Context.LocalBudgetExhausted {
            return false;
        }
    }
    RecordLayeredCatalogFailure(Context, State, &Variable);
    false
}

pub(in crate::Escape) fn SearchLayeredCatalogAccessByPortal(
    Context: &mut LayeredCatalogSearchContext,
    State: &mut LayeredCatalogSelectionState,
    AccessVariables: &[String],
) -> bool {
    if Context.BudgetExhausted || Context.DeadlineExceeded {
        return false;
    }
    let mut BestVariable = None::<String>;
    let mut BestChoices = Vec::<(String, usize)>::new();
    for Variable in AccessVariables {
        if State.SelectedByVariable.contains_key(Variable) {
            continue;
        }
        let Some((PortalName, _PortalValue)) =
            Context.Groups[Variable].first().and_then(|Candidate| {
                Candidate
                    .TemplateRequirements
                    .iter()
                    .find(|(Name, _Value)| Name.starts_with("access-portal:"))
            })
        else {
            Context.FailureNet = Some(Variable.clone());
            return false;
        };
        let Some(SelectedPortalValue) = State.RequirementChoices.get(PortalName) else {
            Context.FailureNet = Some(Variable.clone());
            return false;
        };
        let Some(Choices) = Context
            .AccessChoicesByPortalRequirement
            .get(&(PortalName.clone(), SelectedPortalValue.clone()))
        else {
            Context.FailureNet = Some(Variable.clone());
            return false;
        };
        if Choices.is_empty() {
            Context.FailureNet = Some(Variable.clone());
            return false;
        }
        let mut ViableChoices = Vec::new();
        for (ChoiceVariable, CandidateIndex) in Choices {
            if ChoiceVariable != Variable {
                Context.FailureNet = Some(Variable.clone());
                return false;
            }
            let SelectedCheckpoint = State.SelectedOrder.len();
            let mut NewRequirementNames = Vec::new();
            let Viable = ApplyLayeredCatalogCandidate(
                Context,
                State,
                ChoiceVariable,
                *CandidateIndex,
                &mut NewRequirementNames,
            );
            RollbackLayeredCatalogSelection(
                Context,
                State,
                SelectedCheckpoint,
                &mut NewRequirementNames,
            );
            if Viable {
                ViableChoices.push((ChoiceVariable.clone(), *CandidateIndex));
            }
        }
        if ViableChoices.is_empty() {
            RecordLayeredCatalogFailure(Context, State, Variable);
            return false;
        }
        if BestVariable
            .as_ref()
            .is_none_or(|BestName| (ViableChoices.len(), Variable) < (BestChoices.len(), BestName))
        {
            BestVariable = Some(Variable.clone());
            BestChoices = ViableChoices;
        }
    }
    let Some(Variable) = BestVariable else {
        return true;
    };
    for (ChoiceVariable, CandidateIndex) in BestChoices {
        if ChoiceVariable != Variable {
            Context.FailureNet = Some(Variable.clone());
            return false;
        }
        let SelectedCheckpoint = State.SelectedOrder.len();
        let mut NewRequirementNames = Vec::new();
        if ConsumeLayeredCatalogExpansion(Context, &Variable)
            && ApplyLayeredCatalogCandidate(
                Context,
                State,
                &Variable,
                CandidateIndex,
                &mut NewRequirementNames,
            )
            && SearchLayeredCatalogAccessByPortal(Context, State, AccessVariables)
        {
            return true;
        }
        RollbackLayeredCatalogSelection(
            Context,
            State,
            SelectedCheckpoint,
            &mut NewRequirementNames,
        );
        if Context.BudgetExhausted || Context.DeadlineExceeded {
            return false;
        }
    }
    RecordLayeredCatalogFailure(Context, State, &Variable);
    false
}

pub(in crate::Escape) fn LayeredCatalogGuideVariableForFailure(
    Groups: &BTreeMap<String, Vec<AssignmentCandidate>>,
    FailureVariable: Option<&str>,
) -> Option<String> {
    let FailureVariable = FailureVariable?;
    if FailureVariable.starts_with("__route_guide__:") {
        return Groups
            .contains_key(FailureVariable)
            .then(|| FailureVariable.to_string());
    }
    let OwnerSignal = Groups.get(FailureVariable)?.first()?.OwnerSignal.clone();
    let GuideVariable = format!("__route_guide__:{OwnerSignal}");
    Groups.contains_key(&GuideVariable).then_some(GuideVariable)
}

pub(in crate::Escape) fn LayeredCatalogBlockingSelectedGuide(
    Context: &LayeredCatalogSearchContext,
    State: &LayeredCatalogSelectionState,
    RepairGuideVariables: &BTreeSet<String>,
) -> Option<String> {
    let SelectedGuides = State
        .SelectedByVariable
        .iter()
        .filter(|(Variable, _CandidateIndex)| {
            Variable.starts_with("__route_guide__:") && !RepairGuideVariables.contains(*Variable)
        })
        .map(|(Variable, CandidateIndex)| (Variable, &Context.Groups[Variable][*CandidateIndex]))
        .collect::<Vec<_>>();
    let mut ConflictCounts = BTreeMap::<String, usize>::new();
    for RepairVariable in RepairGuideVariables {
        for Candidate in Context.Groups[RepairVariable].iter().take(32) {
            for (SelectedVariable, SelectedCandidate) in &SelectedGuides {
                if Candidate.Claims.Conflicts(&SelectedCandidate.Claims) {
                    *ConflictCounts
                        .entry((*SelectedVariable).clone())
                        .or_default() += 1;
                }
            }
            for Requirement in Candidate
                .TemplateRequirements
                .iter()
                .filter(|(Name, _Value)| Name.starts_with("access-portal:"))
            {
                for (AccessVariable, AccessCandidateIndex) in Context
                    .AccessChoicesByPortalRequirement
                    .get(Requirement)
                    .into_iter()
                    .flatten()
                {
                    let AccessCandidate = &Context.Groups[AccessVariable][*AccessCandidateIndex];
                    for (SelectedVariable, SelectedCandidate) in &SelectedGuides {
                        if AccessCandidate.Claims.Conflicts(&SelectedCandidate.Claims) {
                            *ConflictCounts
                                .entry((*SelectedVariable).clone())
                                .or_default() += 1;
                        }
                    }
                }
            }
        }
    }
    ConflictCounts
        .into_iter()
        .max_by(|First, Second| First.1.cmp(&Second.1).then_with(|| Second.0.cmp(&First.0)))
        .filter(|(_Variable, Count)| *Count > 0)
        .map(|(Variable, _Count)| Variable)
}
