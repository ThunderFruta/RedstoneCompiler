macro_rules! EnumerateLayeredGuideShapesPhase {
    (
        $TerminalVariables:ident,
        $AccessByVariable:ident,
        $CompleteAccessWitness:ident,
        $RequiredVariables:ident,
        $PortalVariantLimit:ident,
        $Deadline:ident,
        $RoutingYs:ident,
        $DetachedSeedAnchorsByOwner:ident,
        $Signal:ident,
        $MinimumZ:ident,
        $MinimumX:ident,
        $TrackPitch:ident,
        $LaneCount:ident,
        $MaximumShapesPerSignal:ident,
        $BaseValuesByOwner:ident,
        $RequiredWireByOwner:ident,
        $ForeignBlockedNodesByOwner:ident,
        $AccessRampsByPhysicalGuide:ident,
        $MemberIndex:ident,
        $GuideExpansion:ident,
        $GraphAdjacency:ident,
        $IndexedGraph:ident,
        $SharedAccessRampCache:ident,
        $BaseClaimIndex:ident,
        $SourceDetachedAnchorIndex:ident,
        $PreferredAccessWitnessByPortalTuple:ident,
        $AccessWitnessByPhysicalGuide:ident,
        $ClaimBundleRejectionCount:ident,
        $AccessRampConnectivityRejectionCount:ident,
        $AccessRampSelfConflictRejectionCount:ident,
        $AccessRampBaseConflictRejectionCount:ident,
        $AccessWitnessExpansionCount:ident,
        $PoweredWitnessWorkspace:ident,
        $SourceTerminalVariable:ident,
        $SignalShapes:ident
    ) => {
        for (LayerIndex, RoutingY) in $RoutingYs.iter().enumerate() {
            let Domains = $TerminalVariables
                .iter()
                .map(|Variable| {
                    let mut Domain = $AccessByVariable.get(Variable).cloned().unwrap_or_default();
                    Domain.sort_by(|First, Second| {
                        First
                            .IngressY
                            .abs_diff(*RoutingY)
                            .cmp(&Second.IngressY.abs_diff(*RoutingY))
                            .then_with(|| First.Wire.len().cmp(&Second.Wire.len()))
                            .then_with(|| First.IngressY.cmp(&Second.IngressY))
                            .then_with(|| First.Portal.cmp(&Second.Portal))
                            .then_with(|| First.Wire.cmp(&Second.Wire))
                            .then_with(|| First.CandidateId.cmp(&Second.CandidateId))
                    });
                    Domain
                })
                .collect::<Vec<_>>();
            if Domains.iter().any(Vec::is_empty) {
                continue;
            }
            if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE_VERBOSE").is_some() {
                eprintln!(
                    "native layered access domains signal={} routing_y={} values={:?}",
                    $Signal,
                    RoutingY,
                    Domains
                        .iter()
                        .map(|Domain| Domain
                            .iter()
                            .map(|Value| (
                                Value.CandidateId.as_str(),
                                Value.Portal,
                                Value.Wire.len()
                            ))
                            .collect::<Vec<_>>())
                        .collect::<Vec<_>>(),
                );
            }
            let mut PortalTuples = $CompleteAccessWitness
                .as_ref()
                .as_ref()
                .and_then(|Witness| {
                    $TerminalVariables
                        .iter()
                        .map(|Variable| {
                            let CandidateId = Witness.get(Variable)?;
                            $AccessByVariable[Variable]
                                .iter()
                                .copied()
                                .find(|Value| &Value.CandidateId == CandidateId)
                        })
                        .collect::<Option<Vec<_>>>()
                })
                .filter(|Values| LayeredAccessTupleIsSelfLegal(Values))
                .into_iter()
                .collect::<Vec<_>>();
            let mut PrimaryPortalTupleCount;
            if $RequiredVariables.len() > 64 {
                // Match the authoritative large-demand policy: retain a
                // rotated diagonal of the ranked terminal domains, then add
                // bounded single-terminal rank perturbations for high fanout.
                // This preserves access diversity without expanding the full
                // Cartesian product.
                let VariantCount =
                    (*$PortalVariantLimit).min(Domains.iter().map(Vec::len).max().unwrap_or(0));
                let mut SeenPortalIds = PortalTuples
                    .iter()
                    .map(|Values| {
                        Values
                            .iter()
                            .map(|Value| Value.CandidateId.clone())
                            .collect::<Vec<_>>()
                    })
                    .collect::<BTreeSet<_>>();
                for Variant in 0..VariantCount {
                    let mut Candidate: Vec<&DeferredAccessCandidateValue> =
                        Vec::with_capacity(Domains.len());
                    let mut Coherent = true;
                    for (DomainIndex, Domain) in Domains.iter().enumerate() {
                        if let Some(PreviousIndex) = $TerminalVariables[..DomainIndex]
                            .iter()
                            .position(|Value| Value == &$TerminalVariables[DomainIndex])
                        {
                            let PreviousId = &Candidate[PreviousIndex].CandidateId;
                            if let Some(Value) = Domain
                                .iter()
                                .copied()
                                .find(|Value| &Value.CandidateId == PreviousId)
                            {
                                Candidate.push(Value);
                            } else {
                                Coherent = false;
                                break;
                            }
                        } else {
                            Candidate.push(Domain[(Variant + DomainIndex) % Domain.len()]);
                        }
                    }
                    let CandidateIds = Candidate
                        .iter()
                        .map(|Value| Value.CandidateId.clone())
                        .collect::<Vec<_>>();
                    if Coherent
                        && LayeredAccessTupleIsSelfLegal(&Candidate)
                        && SeenPortalIds.insert(CandidateIds)
                    {
                        PortalTuples.push(Candidate);
                    }
                }
                // Preserve the former high-fanout witness pool exactly.  The
                // additional tuples below are replacement witnesses only and
                // must not perturb any previously finite shape.
                if $TerminalVariables.len() >= 5 && !PortalTuples.is_empty() {
                    let Baseline = PortalTuples[0].clone();
                    let MaximumRankOffset = 3usize;
                    'Perturbations: for RankOffset in 1..MaximumRankOffset {
                        for (DomainIndex, Domain) in Domains.iter().enumerate() {
                            let BaselineIndex = Domain
                                .iter()
                                .position(|Value| {
                                    Value.CandidateId == Baseline[DomainIndex].CandidateId
                                })
                                .expect("diagonal portal belongs to its domain");
                            let mut Candidate = Baseline.clone();
                            Candidate[DomainIndex] =
                                Domain[(BaselineIndex + RankOffset) % Domain.len()];
                            let CandidateIds = Candidate
                                .iter()
                                .map(|Value| Value.CandidateId.clone())
                                .collect::<Vec<_>>();
                            if LayeredAccessTupleIsSelfLegal(&Candidate)
                                && SeenPortalIds.insert(CandidateIds)
                            {
                                PortalTuples.push(Candidate);
                                if PortalTuples.len() >= 16 {
                                    break 'Perturbations;
                                }
                            }
                        }
                    }
                }
                PrimaryPortalTupleCount = PortalTuples.len();
                if !PortalTuples.is_empty() && PortalTuples.len() < 16 {
                    let TupleDomains = Domains
                        .iter()
                        .map(|Domain| {
                            let mut Values = Domain.clone();
                            Values.sort_by(|First, Second| {
                                First
                                    .IngressY
                                    .abs_diff(*RoutingY)
                                    .cmp(&Second.IngressY.abs_diff(*RoutingY))
                                    .then_with(|| First.Wire.len().cmp(&Second.Wire.len()))
                                    .then_with(|| First.IngressY.cmp(&Second.IngressY))
                                    .then_with(|| First.Portal.cmp(&Second.Portal))
                                    .then_with(|| First.Wire.cmp(&Second.Wire))
                                    .then_with(|| First.CandidateId.cmp(&Second.CandidateId))
                            });
                            Values
                        })
                        .collect::<Vec<_>>();
                    let TupleScore = |Indices: &[usize]| {
                        let LayerDistance = Indices
                            .iter()
                            .enumerate()
                            .map(|(DomainIndex, CandidateIndex)| {
                                TupleDomains[DomainIndex][*CandidateIndex]
                                    .IngressY
                                    .abs_diff(*RoutingY) as usize
                            })
                            .sum::<usize>();
                        let Length = Indices
                            .iter()
                            .enumerate()
                            .map(|(DomainIndex, CandidateIndex)| {
                                TupleDomains[DomainIndex][*CandidateIndex].Wire.len()
                            })
                            .sum::<usize>();
                        let Ids = Indices
                            .iter()
                            .enumerate()
                            .map(|(DomainIndex, CandidateIndex)| {
                                TupleDomains[DomainIndex][*CandidateIndex]
                                    .CandidateId
                                    .clone()
                            })
                            .collect::<Vec<_>>();
                        (LayerDistance, Length, Ids)
                    };
                    let InitialIndices = vec![0usize; TupleDomains.len()];
                    let (InitialLayerDistance, InitialLength, InitialIds) =
                        TupleScore(&InitialIndices);
                    let mut TupleFrontier = BinaryHeap::from([Reverse((
                        InitialLayerDistance,
                        InitialLength,
                        InitialIds,
                        InitialIndices.clone(),
                    ))]);
                    let mut SeenIndexTuples = HashSet::from([InitialIndices]);
                    let mut CompletedTupleStates = 0usize;
                    while let Some(Reverse((_LayerDistance, _Length, _Ids, Indices))) =
                        TupleFrontier.pop()
                    {
                        if CompletedTupleStates % DEADLINE_CHECK_INTERVAL == 0 && $Deadline.Check()
                        {
                            return Ok(None);
                        }
                        CompletedTupleStates += 1;
                        let Candidate = Indices
                            .iter()
                            .enumerate()
                            .map(|(DomainIndex, CandidateIndex)| {
                                TupleDomains[DomainIndex][*CandidateIndex]
                            })
                            .collect::<Vec<_>>();
                        let Coherent =
                            $TerminalVariables
                                .iter()
                                .enumerate()
                                .all(|(DomainIndex, Variable)| {
                                    $TerminalVariables[..DomainIndex]
                                        .iter()
                                        .position(|Previous| Previous == Variable)
                                        .is_none_or(|PreviousIndex| {
                                            Candidate[PreviousIndex].CandidateId
                                                == Candidate[DomainIndex].CandidateId
                                        })
                                });
                        let CandidateIds = Candidate
                            .iter()
                            .map(|Value| Value.CandidateId.clone())
                            .collect::<Vec<_>>();
                        if Coherent
                            && LayeredAccessTupleIsSelfLegal(&Candidate)
                            && SeenPortalIds.insert(CandidateIds)
                        {
                            PortalTuples.push(Candidate);
                            if PortalTuples.len() >= 16 {
                                break;
                            }
                        }
                        for DomainIndex in 0..TupleDomains.len() {
                            let mut NextIndices = Indices.clone();
                            NextIndices[DomainIndex] += 1;
                            if NextIndices[DomainIndex] >= TupleDomains[DomainIndex].len()
                                || !SeenIndexTuples.insert(NextIndices.clone())
                            {
                                continue;
                            }
                            let (NextLayerDistance, NextLength, NextIds) = TupleScore(&NextIndices);
                            TupleFrontier.push(Reverse((
                                NextLayerDistance,
                                NextLength,
                                NextIds,
                                NextIndices,
                            )));
                        }
                    }
                }
            } else {
                // Preserve layer-distinct access worlds before filling the
                // remaining finite tuple frontier by cost.  A globally nearest
                // Cartesian prefix can contain only one ingress layer and erase
                // the mixed-layer stub bundle needed by an otherwise legal guide.
                // The rotated diagonal is deterministic and every retained tuple
                // remains an exact physical access choice.
                let VariantCount =
                    (*$PortalVariantLimit).min(Domains.iter().map(Vec::len).max().unwrap_or(0));
                let mut SeenPortalIds = PortalTuples
                    .iter()
                    .map(|Values| {
                        Values
                            .iter()
                            .map(|Value| Value.CandidateId.clone())
                            .collect::<Vec<_>>()
                    })
                    .collect::<BTreeSet<_>>();
                for Variant in 0..VariantCount {
                    let Candidate = Domains
                        .iter()
                        .enumerate()
                        .map(|(DomainIndex, Domain)| Domain[(Variant + DomainIndex) % Domain.len()])
                        .collect::<Vec<_>>();
                    let CandidateIds = Candidate
                        .iter()
                        .map(|Value| Value.CandidateId.clone())
                        .collect::<Vec<_>>();
                    if LayeredAccessTupleIsSelfLegal(&Candidate)
                        && SeenPortalIds.insert(CandidateIds)
                    {
                        PortalTuples.push(Candidate);
                    }
                }
                PrimaryPortalTupleCount = PortalTuples.len();
                // Fill replacement witnesses from the exact least-cost Cartesian
                // frontier.  These are tried only when a primary tuple cannot
                // certify the requested guide, so they do not perturb an already
                // valid primary physical shape.
                let TupleDomains = Domains
                    .iter()
                    .map(|Domain| {
                        let mut Values = Domain.clone();
                        Values.sort_by(|First, Second| {
                            First
                                .IngressY
                                .abs_diff(*RoutingY)
                                .cmp(&Second.IngressY.abs_diff(*RoutingY))
                                .then_with(|| First.Wire.len().cmp(&Second.Wire.len()))
                                .then_with(|| First.IngressY.cmp(&Second.IngressY))
                                .then_with(|| First.Portal.cmp(&Second.Portal))
                                .then_with(|| First.Wire.cmp(&Second.Wire))
                                .then_with(|| First.CandidateId.cmp(&Second.CandidateId))
                        });
                        Values
                    })
                    .collect::<Vec<_>>();
                let TupleScore = |Indices: &[usize]| {
                    let LayerDistance = Indices
                        .iter()
                        .enumerate()
                        .map(|(DomainIndex, CandidateIndex)| {
                            TupleDomains[DomainIndex][*CandidateIndex]
                                .IngressY
                                .abs_diff(*RoutingY) as usize
                        })
                        .sum::<usize>();
                    let Length = Indices
                        .iter()
                        .enumerate()
                        .map(|(DomainIndex, CandidateIndex)| {
                            TupleDomains[DomainIndex][*CandidateIndex].Wire.len()
                        })
                        .sum::<usize>();
                    let Ids = Indices
                        .iter()
                        .enumerate()
                        .map(|(DomainIndex, CandidateIndex)| {
                            TupleDomains[DomainIndex][*CandidateIndex]
                                .CandidateId
                                .clone()
                        })
                        .collect::<Vec<_>>();
                    (LayerDistance, Length, Ids)
                };
                let InitialIndices = vec![0usize; Domains.len()];
                let (InitialLayerDistance, InitialLength, InitialIds) = TupleScore(&InitialIndices);
                let mut TupleFrontier = BinaryHeap::from([Reverse((
                    InitialLayerDistance,
                    InitialLength,
                    InitialIds,
                    InitialIndices.clone(),
                ))]);
                let mut SeenIndexTuples = HashSet::from([InitialIndices]);
                let mut CompletedTupleStates = 0usize;
                while let Some(Reverse((_LayerDistance, _Length, _Ids, Indices))) =
                    TupleFrontier.pop()
                {
                    if CompletedTupleStates % DEADLINE_CHECK_INTERVAL == 0 && $Deadline.Check() {
                        return Ok(None);
                    }
                    CompletedTupleStates += 1;
                    let Candidate = Indices
                        .iter()
                        .enumerate()
                        .map(|(DomainIndex, CandidateIndex)| {
                            TupleDomains[DomainIndex][*CandidateIndex]
                        })
                        .collect::<Vec<_>>();
                    let Coherent =
                        $TerminalVariables
                            .iter()
                            .enumerate()
                            .all(|(DomainIndex, Variable)| {
                                $TerminalVariables[..DomainIndex]
                                    .iter()
                                    .position(|Previous| Previous == Variable)
                                    .is_none_or(|PreviousIndex| {
                                        Candidate[PreviousIndex].CandidateId
                                            == Candidate[DomainIndex].CandidateId
                                    })
                            });
                    let CandidateIds = Candidate
                        .iter()
                        .map(|Value| Value.CandidateId.clone())
                        .collect::<Vec<_>>();
                    if Coherent
                        && LayeredAccessTupleIsSelfLegal(&Candidate)
                        && SeenPortalIds.insert(CandidateIds)
                    {
                        PortalTuples.push(Candidate);
                        if PortalTuples.len() >= 16 {
                            break;
                        }
                    }
                    for DomainIndex in 0..Domains.len() {
                        let mut NextIndices = Indices.clone();
                        NextIndices[DomainIndex] += 1;
                        if NextIndices[DomainIndex] >= TupleDomains[DomainIndex].len()
                            || !SeenIndexTuples.insert(NextIndices.clone())
                        {
                            continue;
                        }
                        let (NextLayerDistance, NextLength, NextIds) = TupleScore(&NextIndices);
                        TupleFrontier.push(Reverse((
                            NextLayerDistance,
                            NextLength,
                            NextIds,
                            NextIndices,
                        )));
                    }
                }
                if PrimaryPortalTupleCount == 0 {
                    PrimaryPortalTupleCount = (*$PortalVariantLimit).min(PortalTuples.len());
                }
            }
            EnumerateLayeredGuidePhysicalShapesPhase!(
                Domains,
                PortalTuples,
                PrimaryPortalTupleCount,
                LayerIndex,
                RoutingY,
                $AccessByVariable,
                $AccessRampBaseConflictRejectionCount,
                $AccessRampConnectivityRejectionCount,
                $AccessRampSelfConflictRejectionCount,
                $AccessRampsByPhysicalGuide,
                $AccessWitnessByPhysicalGuide,
                $AccessWitnessExpansionCount,
                $BaseClaimIndex,
                $BaseValuesByOwner,
                $ClaimBundleRejectionCount,
                $Deadline,
                $DetachedSeedAnchorsByOwner,
                $ForeignBlockedNodesByOwner,
                $GraphAdjacency,
                $GuideExpansion,
                $IndexedGraph,
                $LaneCount,
                $MaximumShapesPerSignal,
                $MemberIndex,
                $MinimumX,
                $MinimumZ,
                $PortalVariantLimit,
                $PoweredWitnessWorkspace,
                $PreferredAccessWitnessByPortalTuple,
                $RequiredWireByOwner,
                $RoutingYs,
                $SharedAccessRampCache,
                $Signal,
                $SignalShapes,
                $SourceDetachedAnchorIndex,
                $SourceTerminalVariable,
                $TerminalVariables,
                $TrackPitch
            );
        }
    };
}
