macro_rules! PrepareLayeredCatalogSolverPhase {
    (
        $Groups:ident,
        $ResourceCount:ident,
        $CrossAirByWire:ident,
        $ExternalWarmSelections:ident,
        $MaximumExpansionCount:ident,
        $SharedExpansionCount:ident,
        $Deadline:ident,
        $AccessVariables:ident,
        $GuideVariables:ident,
        $WarmSelections:ident,
        $WarmCandidateIdByVariable:ident,
        $AccessChoicesByPortalRequirement:ident,
        $PrecomputedRepairGuideVariables:ident,
        $AccessChoicesByStubRequirement:ident
    ) => {
        let UnsupportedVariables = $Groups
            .keys()
            .filter(|Variable| {
                !Variable.starts_with("__access_terminal__:")
                    && !Variable.starts_with("__route_guide__:")
                    && !Variable.starts_with("__base_claim__:")
                    && Variable.as_str() != "__fixed_base_claim_conflict__"
            })
            .cloned()
            .collect::<Vec<_>>();
        if !UnsupportedVariables.is_empty() {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "layered access catalog contains unsupported variable kinds: {:?}",
                UnsupportedVariables,
            )));
        }
        let GuidePortalChoicesByRequirement = $Groups
            .iter()
            .filter(|(Variable, _Values)| Variable.starts_with("__route_guide__:"))
            .flat_map(|(_Variable, Values)| Values)
            .flat_map(|Candidate| Candidate.TemplateRequirements.iter())
            .filter(|(Name, _Value)| Name.starts_with("access-portal:"))
            .fold(
                HashMap::<String, HashSet<String>>::new(),
                |mut Result, (Name, Value)| {
                    Result
                        .entry(Name.clone())
                        .or_default()
                        .insert(Value.clone());
                    Result
                },
            );
        for (Variable, Values) in $Groups.iter_mut() {
            if !Variable.starts_with("__access_terminal__:") {
                continue;
            }
            Values.retain(|Candidate| {
                Candidate.TemplateRequirements.iter().all(|(Name, Value)| {
                    !Name.starts_with("access-portal:")
                        || GuidePortalChoicesByRequirement
                            .get(Name)
                            .is_none_or(|Choices| Choices.contains(Value))
                })
            });
        }
        let AccessPortalChoices = $Groups
            .iter()
            .filter(|(Variable, _Values)| Variable.starts_with("__access_terminal__:"))
            .flat_map(|(_Variable, Values)| Values)
            .flat_map(|Candidate| Candidate.TemplateRequirements.iter())
            .filter(|(Name, _Value)| Name.starts_with("access-portal:"))
            .cloned()
            .collect::<HashSet<_>>();
        for (Variable, Values) in $Groups.iter_mut() {
            if !Variable.starts_with("__route_guide__:") {
                continue;
            }
            Values.retain(|Candidate| {
                Candidate.TemplateRequirements.iter().all(|Requirement| {
                    !Requirement.0.starts_with("access-portal:")
                        || AccessPortalChoices.contains(Requirement)
                })
            });
        }
        if let Some((Variable, _Values)) =
            $Groups.iter().find(|(_Variable, Values)| Values.is_empty())
        {
            let ExpansionCount = $SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
            return Ok(RoutingAssignmentResult {
                Success: false,
                SelectedCandidateIds: Vec::new(),
                ExpansionCount,
                BudgetExhausted: false,
                DeadlineExceeded: $Deadline.WasExceeded(),
                CompletedWork: ExpansionCount,
                FailureNet: Some(Variable.clone()),
                ConflictSignals: Vec::new(),
                ConflictResourceIndices: Vec::new(),
                PairwiseIncompatibleSignals: Vec::new(),
                PairwiseCompatibilityComplete: !$Deadline.WasExceeded(),
            });
        }
        for Values in $Groups.values_mut() {
            if !SortCandidatesWithDeadline(Values, $Deadline) {
                let ExpansionCount =
                    $SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
                return Ok(RoutingAssignmentResult {
                    Success: false,
                    SelectedCandidateIds: Vec::new(),
                    ExpansionCount,
                    BudgetExhausted: false,
                    DeadlineExceeded: true,
                    CompletedWork: ExpansionCount,
                    FailureNet: None,
                    ConflictSignals: Vec::new(),
                    ConflictResourceIndices: Vec::new(),
                    PairwiseIncompatibleSignals: Vec::new(),
                    PairwiseCompatibilityComplete: false,
                });
            }
        }
        if std::env::var_os("RCS_EXPERIMENTAL_SINGLE_WITNESS_BUNDLES").is_some() {
            let Some((mut BundledGroups, BundleDecodeMap)) =
                BuildUniqueLayeredCatalogBundleGroups($Groups, $Deadline)?
            else {
                let ExpansionCount =
                    $SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
                return Ok(RoutingAssignmentResult {
                    Success: false,
                    SelectedCandidateIds: Vec::new(),
                    ExpansionCount,
                    BudgetExhausted: false,
                    DeadlineExceeded: true,
                    CompletedWork: ExpansionCount,
                    FailureNet: None,
                    ConflictSignals: Vec::new(),
                    ConflictResourceIndices: Vec::new(),
                    PairwiseIncompatibleSignals: Vec::new(),
                    PairwiseCompatibilityComplete: false,
                });
            };
            let ExpansionCountBeforeBundledSolve =
                $SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
            let mut BundledAssignment =
                PlanAuthoritativeCandidateGroupsWithInitialExpansionDeadlineAndCrossAir(
                    &mut BundledGroups,
                    $ResourceCount,
                    ExpansionCountBeforeBundledSolve,
                    $MaximumExpansionCount,
                    $Deadline.clone(),
                    true,
                    false,
                    Some($SharedExpansionCount),
                    Some($CrossAirByWire.as_slice()),
                )?;
            if BundledAssignment.Success {
                BundledAssignment.SelectedCandidateIds = BundledAssignment
                    .SelectedCandidateIds
                    .iter()
                    .flat_map(|(Variable, CandidateId)| {
                        BundleDecodeMap
                            .get(&(Variable.clone(), CandidateId.clone()))
                            .cloned()
                            .unwrap_or_else(|| vec![(Variable.clone(), CandidateId.clone())])
                    })
                    .collect();
                BundledAssignment.SelectedCandidateIds.sort();
                BundledAssignment.SelectedCandidateIds.dedup();
            }
            return Ok(BundledAssignment);
        }
        let $AccessVariables = $Groups
            .keys()
            .filter(|Variable| Variable.starts_with("__access_terminal__:"))
            .cloned()
            .collect::<Vec<_>>();
        let mut $GuideVariables = $Groups
            .keys()
            .filter(|Variable| Variable.starts_with("__route_guide__:"))
            .cloned()
            .collect::<Vec<_>>();
        // A caller-provided complete witness may seed exact validation.  The
        // production batch otherwise enters the portal-aware bounded search
        // directly; constructing a member-local all-pairs compatibility matrix
        // duplicates the exact claim checks and dominates portfolio runtime.
        let mut $WarmSelections = if let Some($ExternalWarmSelections) = $ExternalWarmSelections {
            $ExternalWarmSelections.to_vec()
        } else {
            Vec::new()
        };
        let mut $WarmCandidateIdByVariable =
            $WarmSelections.iter().cloned().collect::<HashMap<_, _>>();
        for (Variable, Values) in $Groups.iter_mut() {
            let Some(PreferredCandidateId) = $WarmCandidateIdByVariable.get(Variable) else {
                continue;
            };
            if let Some(PreferredIndex) = Values
                .iter()
                .position(|Candidate| &Candidate.CandidateId == PreferredCandidateId)
            {
                Values[..=PreferredIndex].rotate_right(1);
            }
        }
        let $AccessChoicesByPortalRequirement = $AccessVariables
            .iter()
            .flat_map(|Variable| {
                $Groups[Variable]
                    .iter()
                    .enumerate()
                    .flat_map(move |(CandidateIndex, Candidate)| {
                        Candidate
                            .TemplateRequirements
                            .iter()
                            .filter(|(Name, _Value)| Name.starts_with("access-portal:"))
                            .map(move |(Name, Value)| {
                                (
                                    (Name.clone(), Value.clone()),
                                    (Variable.clone(), CandidateIndex),
                                )
                            })
                    })
            })
            .fold(
                HashMap::<(String, String), Vec<(String, usize)>>::new(),
                |mut Result, (Requirement, Choice)| {
                    Result.entry(Requirement).or_default().push(Choice);
                    Result
                },
            );
        if $ExternalWarmSelections.is_none() {
            let ExpansionCountBeforeIndexedSolve =
                $SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
            // This first solve supplies a deterministic seed by selecting each
            // guide together with one exact referenced access tuple.  The retained
            // support basis is not an infeasibility proof; the exact powered
            // closure and portal-aware repair below remain authoritative.
            let IndexedAssignment =
                PlanAuthoritativeCandidateGroupsWithInitialExpansionDeadlineAndCrossAir(
                    $Groups,
                    $ResourceCount,
                    ExpansionCountBeforeIndexedSolve,
                    $MaximumExpansionCount,
                    $Deadline.clone(),
                    true,
                    false,
                    Some($SharedExpansionCount),
                    Some($CrossAirByWire.as_slice()),
                )?;
            if !IndexedAssignment.Success {
                return Ok(IndexedAssignment);
            }
            return Ok(IndexedAssignment);
        }
        let Some($PrecomputedRepairGuideVariables) =
            CloseLayeredCatalogWarmGuideTuples($Groups, &$WarmSelections, $Deadline)
        else {
            let ExpansionCount = $SharedExpansionCount.load(std::sync::atomic::Ordering::SeqCst);
            return Ok(RoutingAssignmentResult {
                Success: false,
                SelectedCandidateIds: Vec::new(),
                ExpansionCount,
                BudgetExhausted: false,
                DeadlineExceeded: true,
                CompletedWork: ExpansionCount,
                FailureNet: None,
                ConflictSignals: Vec::new(),
                ConflictResourceIndices: Vec::new(),
                PairwiseIncompatibleSignals: Vec::new(),
                PairwiseCompatibilityComplete: false,
            });
        };
        let $AccessChoicesByStubRequirement = $AccessVariables
            .iter()
            .flat_map(|Variable| {
                $Groups[Variable]
                    .iter()
                    .enumerate()
                    .flat_map(move |(CandidateIndex, Candidate)| {
                        Candidate
                            .TemplateRequirements
                            .iter()
                            .filter(|(Name, _Value)| Name.starts_with("access-stub:"))
                            .map(move |(Name, Value)| {
                                (
                                    (Name.clone(), Value.clone()),
                                    (Variable.clone(), CandidateIndex),
                                )
                            })
                    })
            })
            .fold(
                HashMap::<(String, String), Vec<(String, usize)>>::new(),
                |mut Result, (Requirement, Choice)| {
                    Result.entry(Requirement).or_default().push(Choice);
                    Result
                },
            );
        // Compact guide values bind exact portals, while access candidates remain
        // independent variables.  The portal-aware search below composes those
        // values lazily with owner-aware claim checks, avoiding an expanded
        // guide-by-stub bundle domain.
    };
}
