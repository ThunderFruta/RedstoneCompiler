macro_rules! EnumerateLayeredGuidePhysicalShapesPhase {
    (
        $Domains:ident,
        $PortalTuples:ident,
        $PrimaryPortalTupleCount:ident,
        $LayerIndex:ident,
        $RoutingY:ident,
        $AccessByVariable:ident,
        $AccessRampBaseConflictRejectionCount:ident,
        $AccessRampConnectivityRejectionCount:ident,
        $AccessRampSelfConflictRejectionCount:ident,
        $AccessRampsByPhysicalGuide:ident,
        $AccessWitnessByPhysicalGuide:ident,
        $AccessWitnessExpansionCount:ident,
        $BaseClaimIndex:ident,
        $BaseValuesByOwner:ident,
        $ClaimBundleRejectionCount:ident,
        $Deadline:ident,
        $DetachedSeedAnchorsByOwner:ident,
        $ForeignBlockedNodesByOwner:ident,
        $GraphAdjacency:ident,
        $GuideExpansion:ident,
        $IndexedGraph:ident,
        $LaneCount:ident,
        $MaximumShapesPerSignal:ident,
        $MemberIndex:ident,
        $MinimumX:ident,
        $MinimumZ:ident,
        $PortalVariantLimit:ident,
        $PoweredWitnessWorkspace:ident,
        $PreferredAccessWitnessByPortalTuple:ident,
        $RequiredWireByOwner:ident,
        $RoutingYs:ident,
        $SharedAccessRampCache:ident,
        $Signal:ident,
        $SignalShapes:ident,
        $SourceDetachedAnchorIndex:ident,
        $SourceTerminalVariable:ident,
        $TerminalVariables:ident,
        $TrackPitch:ident
    ) => {
            if $PrimaryPortalTupleCount == 0 {
                continue;
            }
            let PhysicalPortalVariantCount =
                (*$PortalVariantLimit).min($PrimaryPortalTupleCount);
            for Variant in 0..PhysicalPortalVariantCount {
                let BaseTuple = &$PortalTuples[Variant];
                let BaseTerminals = BaseTuple
                    .iter()
                    .map(|Value| {
                        let PositionValue = Value.Portal;
                        (PositionValue.0, PositionValue.2)
                    })
                    .chain(
                        $DetachedSeedAnchorsByOwner
                            .get($Signal.as_str())
                            .into_iter()
                            .flat_map(|Values| Values.iter())
                            .filter_map(|Path| Path.last())
                            .map(|PositionValue| {
                                (PositionValue.0, PositionValue.2)
                            }),
                    )
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect::<Vec<_>>();
                let XSpan = BaseTerminals.iter().map(|Value| Value.0).max().unwrap()
                    - BaseTerminals.iter().map(|Value| Value.0).min().unwrap();
                let ZSpan = BaseTerminals.iter().map(|Value| Value.1).max().unwrap()
                    - BaseTerminals.iter().map(|Value| Value.1).min().unwrap();
                let PreferredAxis = if XSpan >= ZSpan { "X" } else { "Z" };
                for (AxisIndex, Axis) in [
                    PreferredAxis,
                    if PreferredAxis == "X" { "Z" } else { "X" },
                ]
                .into_iter()
                .enumerate()
                {
                    let mut Coordinates = BaseTerminals
                        .iter()
                        .map(|Value| if Axis == "X" { Value.1 } else { Value.0 })
                        .collect::<Vec<_>>();
                    Coordinates.sort_unstable();
                    let Center = Coordinates[Coordinates.len() / 2];
                    let TrackAnchor = if Axis == "X" { *$MinimumZ } else { *$MinimumX };
                    let AlignedCenter = TrackAnchor
                        + (Center - TrackAnchor + (*$TrackPitch as i32) / 2)
                            .div_euclid(*$TrackPitch as i32)
                            * (*$TrackPitch as i32);
                    let LaneValues = CandidateLayeredGuideLanes(
                        AlignedCenter,
                        *$LaneCount,
                        *$TrackPitch as i32,
                    );
                    for (LaneIndex, Lane) in LaneValues.into_iter().enumerate() {
                        let PortalPhase = 1 + AxisIndex * 3 + LaneIndex;
                        let PrimaryTupleIndex =
                            (Variant + PortalPhase) % $PrimaryPortalTupleCount;
                        let ShapeCount = (PhysicalPortalVariantCount * 2 * *$LaneCount).max(1);
                        let ShapeIndex = LaneIndex
                            + *$LaneCount * (Variant + PhysicalPortalVariantCount * AxisIndex);
                        let PortalShapeRank =
                            (ShapeIndex + ShapeCount - ($LayerIndex % ShapeCount)) % ShapeCount;
                        let PerLayerRequestLimit = $MaximumShapesPerSignal
                            .saturating_add($RoutingYs.len() - 1)
                            / $RoutingYs.len();
                        if PortalShapeRank >= PerLayerRequestLimit {
                            continue;
                        }
                        let FallbackTupleCount =
                            $PortalTuples.len().saturating_sub($PrimaryPortalTupleCount);
                        let FallbackStart = if FallbackTupleCount == 0 {
                            0
                        } else {
                            (ShapeIndex + $LayerIndex) % FallbackTupleCount
                        };
                        let TupleIndices = std::iter::once(PrimaryTupleIndex)
                            .chain((0..FallbackTupleCount).map(|FallbackOffset| {
                                $PrimaryPortalTupleCount
                                    + (FallbackStart + FallbackOffset) % FallbackTupleCount
                            }))
                            .collect::<Vec<_>>();
                        for (TupleAttemptIndex, TupleIndex) in
                            TupleIndices.into_iter().enumerate()
                        {
                        let PortalTuple = &$PortalTuples[TupleIndex];
                        let Terminals = PortalTuple
                            .iter()
                            .map(|Value| {
                                let PositionValue = Value.Portal;
                                (PositionValue.0, PositionValue.2)
                            })
                            .chain(
                                $DetachedSeedAnchorsByOwner
                                    .get($Signal.as_str())
                                    .into_iter()
                                    .flat_map(|Values| Values.iter())
                                    .filter_map(|Path| Path.last())
                                    .map(|PositionValue| {
                                        (PositionValue.0, PositionValue.2)
                                    }),
                            )
                            .collect::<BTreeSet<_>>()
                            .into_iter()
                            .collect::<Vec<_>>();
                        let Guide = BuildLayeredGuideSpine(
                            &Terminals,
                            Axis,
                            Lane,
                            *$RoutingY,
                        );
                        let PrimaryCandidateId = format!(
                            "__native_guide__:{}:{}:{}:{}:{}:{}",
                            $Signal, $LayerIndex, Variant, Axis, Lane, PortalShapeRank,
                        );
                        let CandidateId = if TupleAttemptIndex == 0 {
                            PrimaryCandidateId
                        } else {
                            format!("{}:fallback:{}", PrimaryCandidateId, TupleIndex)
                        };
                        let PortalIdentity =
                            PortalTuple.iter().map(|Value| Value.Portal).collect::<Vec<_>>();
                        let PhysicalGuideKey = (
                            $LayerIndex,
                            Axis.to_string(),
                            Lane,
                            PortalIdentity.clone(),
                        );
                        let SameOwnerBaseValues = $BaseValuesByOwner
                            .get($Signal)
                            .map(Vec::as_slice)
                            .unwrap_or(&[]);
                        let RequiredWire = &$RequiredWireByOwner[$Signal];
                        let ForeignBlockedNodes = &$ForeignBlockedNodesByOwner[$Signal];
                        let DetachedSeedAccessPaths = $DetachedSeedAnchorsByOwner
                            .get($Signal.as_str())
                            .copied()
                            .unwrap_or(&[]);
                        let AccessRamps = if let Some(Cached) =
                            $AccessRampsByPhysicalGuide.get(&PhysicalGuideKey)
                        {
                            Cached.clone()
                        } else {
                            let CacheKey = LayeredGuideAccessRampCacheKey {
                                $MemberIndex,
                                $LayerIndex,
                                Axis: Axis.to_string(),
                                Lane,
                                PortalIdentity: PortalIdentity.clone(),
                                Guide: Guide.clone(),
                                $GuideExpansion: *$GuideExpansion,
                                RequiredWire: RequiredWire
                                    .iter()
                                    .copied()
                                    .collect::<BTreeSet<_>>()
                                    .into_iter()
                                    .collect(),
                                ForeignBlockedNodes: ForeignBlockedNodes
                                    .iter()
                                    .copied()
                                    .collect::<BTreeSet<_>>()
                                    .into_iter()
                                    .collect(),
                                OwnerSignal: $Signal.clone(),
                                DetachedSeedAccessPaths:
                                    DetachedSeedAccessPaths.to_vec(),
                                $SourceDetachedAnchorIndex:
                                    *$SourceDetachedAnchorIndex,
                            };
                            let CacheCell = $SharedAccessRampCache.GetCell(CacheKey);
                            let Complete = if let Some(Cached) = CacheCell.get() {
                                Cached.clone()
                            } else {
                                let Computed = BuildLayeredGuideNecessaryAccessRamps(
                                        &$GraphAdjacency,
                                        &$IndexedGraph,
                                        $SharedAccessRampCache,
                                        &Guide,
                                        *$GuideExpansion,
                                        PortalTuple,
                                        &$BaseClaimIndex,
                                        RequiredWire,
                                        ForeignBlockedNodes,
                                        $Signal,
                                        DetachedSeedAccessPaths,
                                        *$SourceDetachedAnchorIndex,
                                        $Deadline,
                                    );
                                // An outer None means the bounded proof did not
                                // complete.  It is not a negative certificate
                                // and must never poison later exact reuse.
                                if Computed.is_some() {
                                    let _ = CacheCell.set(Computed.clone());
                                    CacheCell
                                        .get()
                                        .cloned()
                                        .unwrap_or(Computed)
                                } else {
                                    Computed
                                }
                            };
                            let Some(Complete) = Complete else {
                                return Ok(None);
                            };
                            $AccessRampsByPhysicalGuide
                                .insert(PhysicalGuideKey.clone(), Complete.clone());
                            Complete
                        };
                        let Some((
                            PhysicalGuide,
                            AccessRamps,
                            mut DetailedHintPaths,
                            _InitialPoweredCorridorHint,
                        )) = AccessRamps else {
                            $AccessRampConnectivityRejectionCount += 1;
                            continue;
                        };
                        let CombinedGuideWire = PhysicalGuide
                            .iter()
                            .copied()
                            // Mixed exterior/internal nets own their selected
                            // stub and exact fixed ramps at compact selection.
                            // Wholly internal nets have no independent stub
                            // variable, so their detached branches remain
                            // selected-world seeds instead of synthetic guide
                            // capacity.
                            .chain(
                                (!PortalTuple.is_empty())
                                    .then_some(
                                        DetachedSeedAccessPaths
                                            .iter()
                                            .flatten()
                                            .copied(),
                                    )
                                    .into_iter()
                                    .flatten(),
                            )
                            .chain(
                                (!PortalTuple.is_empty())
                                    .then_some(AccessRamps.iter().flatten().copied())
                                    .into_iter()
                                    .flatten(),
                            )
                            .collect::<BTreeSet<_>>()
                            .into_iter()
                            .collect::<Vec<_>>();
                        let Some(Claims) = BuildDeferredAccessCandidate(
                            format!("__route_guide__:{}", $Signal),
                            CandidateId.clone(),
                            $Signal.clone(),
                            *$RoutingY,
                            CombinedGuideWire,
                        ) else {
                            if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE_SIGNAL")
                                .ok()
                                .as_deref()
                                == Some($Signal.as_str())
                                && Lane == -6
                            {
                                let CombinedWire = PhysicalGuide
                                    .iter()
                                    .copied()
                                    .chain(
                                        DetachedSeedAccessPaths
                                            .iter()
                                            .flatten()
                                            .copied(),
                                    )
                                    .chain(AccessRamps.iter().flatten().copied())
                                    .collect::<BTreeSet<_>>();
                                let SupportWireConflicts = CombinedWire
                                    .iter()
                                    .filter_map(|(X, Y, Z)| {
                                        let Support = (*X, Y - 1, *Z);
                                        CombinedWire.contains(&Support).then_some((
                                            (*X, *Y, *Z),
                                            Support,
                                        ))
                                    })
                                    .collect::<Vec<_>>();
                                eprintln!(
                                    "native layered ramp self-conflict signal={} lane={} guide={:?} detached={:?} ramps={:?} support_wire={:?} guide_legal={} ramp_legal={:?}",
                                    $Signal,
                                    Lane,
                                    PhysicalGuide,
                                    DetachedSeedAccessPaths,
                                    AccessRamps,
                                    SupportWireConflicts,
                                    BuildDeferredAccessCandidate(
                                        "guide".to_string(),
                                        "guide".to_string(),
                                        $Signal.clone(),
                                        *$RoutingY,
                                        PhysicalGuide.clone(),
                                    )
                                    .is_some(),
                                    AccessRamps
                                        .iter()
                                        .map(|Path| BuildDeferredAccessCandidate(
                                            "ramp".to_string(),
                                            "ramp".to_string(),
                                            $Signal.clone(),
                                            *$RoutingY,
                                            Path.clone(),
                                        )
                                        .is_some())
                                        .collect::<Vec<_>>(),
                                );
                            }
                            $AccessRampSelfConflictRejectionCount += 1;
                            continue;
                        };
                        if $BaseClaimIndex.Conflicts(&Claims) {
                            if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE_SIGNAL")
                                .ok()
                                .as_deref()
                                == Some($Signal.as_str())
                            {
                                let WireConflicts = Claims
                                    .Wire
                                    .iter()
                                    .copied()
                                    .filter(|PositionValue| {
                                        $BaseClaimIndex.Support.contains(PositionValue)
                                            || $BaseClaimIndex.Air.contains(PositionValue)
                                            || $BaseClaimIndex
                                                .ElectricalOwners
                                                .get(PositionValue)
                                                .is_some_and(|Owners| {
                                                    Owners.iter().any(|Owner| Owner != $Signal)
                                                })
                                    })
                                    .collect::<Vec<_>>();
                                let SupportConflicts = Claims
                                    .Support
                                    .iter()
                                    .copied()
                                    .filter(|PositionValue| {
                                        $BaseClaimIndex.Wire.contains(PositionValue)
                                            || $BaseClaimIndex.Air.contains(PositionValue)
                                    })
                                    .collect::<Vec<_>>();
                                let AirConflicts = Claims
                                    .Air
                                    .iter()
                                    .copied()
                                    .filter(|PositionValue| {
                                        $BaseClaimIndex.Support.contains(PositionValue)
                                            || $BaseClaimIndex.Wire.contains(PositionValue)
                                    })
                                    .collect::<Vec<_>>();
                                eprintln!(
                                    "native layered final base conflict signal={} lane={} wire={:?} support={:?} air={:?}",
                                    $Signal,
                                    Lane,
                                    WireConflicts,
                                    SupportConflicts,
                                    AirConflicts,
                                );
                            }
                            $AccessRampBaseConflictRejectionCount += 1;
                            continue;
                        }
                        let (
                            AccessWitnessRequirementSets,
                            SupportedAccessChoices,
                        ) = if let Some(Cached) =
                            $AccessWitnessByPhysicalGuide.get(&PhysicalGuideKey)
                        {
                            Cached.clone()
                        } else {
                            let (
                                Complete,
                                SupportedChoices,
                                WitnessExpansionCount,
                            ) = match LayeredGuideHasSelfLegalAccessBundle(
                                &Claims,
                                SameOwnerBaseValues,
                                $TerminalVariables,
                                PortalTuple,
                                &$Domains,
                                $PreferredAccessWitnessByPortalTuple
                                    .get(&PortalIdentity)
                                    .map(Vec::as_slice)
                                    .unwrap_or(&[]),
                                $Deadline,
                            ) {
                                Ok(Value) => Value,
                                Err(()) => return Ok(None),
                            };
                            $AccessWitnessExpansionCount = $AccessWitnessExpansionCount
                                .saturating_add(WitnessExpansionCount);
                            let Complete = Arc::new(Complete);
                            $AccessWitnessByPhysicalGuide
                                .insert(
                                    PhysicalGuideKey.clone(),
                                    (
                                        Complete.clone(),
                                        SupportedChoices.clone(),
                                    ),
                                );
                            (Complete, SupportedChoices)
                        };
                        if AccessWitnessRequirementSets.is_empty() {
                            $ClaimBundleRejectionCount += 1;
                            continue;
                        }
                        let CachedWitnesses = $PreferredAccessWitnessByPortalTuple
                            .entry(PortalIdentity)
                            .or_default();
                        if CachedWitnesses.is_empty() {
                            CachedWitnesses.extend(
                                AccessWitnessRequirementSets
                                    .iter()
                                    .take(1)
                                    .cloned(),
                            );
                        }
                        if let Some(AccessWitnessRequirements) =
                            AccessWitnessRequirementSets.first()
                        {
                            let SelectedAccessValues = AccessWitnessRequirements
                                .iter()
                                .map(|(Variable, CandidateId)| {
                                    $AccessByVariable.get(Variable).and_then(|Values| {
                                        Values.iter().copied().find(|Value| {
                                            Value.CandidateId == *CandidateId
                                        })
                                    })
                                })
                                .collect::<Option<Vec<_>>>();
                            let Some(SelectedAccessValues) = SelectedAccessValues else {
                                return Err(pyo3::exceptions::PyValueError::new_err(
                                    "layered powered witness references an unknown access value",
                                ));
                            };
                            let Some(PoweredWitness) =
                                LayeredGuideAccessBundleHasPoweredTreeWitness(
                                    &$IndexedGraph,
                                    &mut $PoweredWitnessWorkspace,
                                    &Claims,
                                    &DetailedHintPaths,
                                    $TerminalVariables,
                                    &SelectedAccessValues,
                                    DetachedSeedAccessPaths,
                                    $SourceTerminalVariable.as_deref(),
                                    *$SourceDetachedAnchorIndex,
                                    $Deadline,
                                )
                            else {
                                return Ok(None);
                            };
                            if std::env::var("RCS_DEBUG_LAYERED_POWERED_SIGNAL")
                                .ok()
                                .is_some_and(|DebugSignal| DebugSignal == *$Signal)
                                && CandidateId.contains(":6:0:X:41:0:fallback:10")
                            {
                                eprintln!(
                                    "native layered powered witness detail signal={} candidate={} terminal_count={} requirement_count={} selected_count={} selected_wire_lengths={:?} source={:?} detached_count={} source_detached={:?} witness={:?}",
                                    $Signal,
                                    CandidateId,
                                    $TerminalVariables.len(),
                                    AccessWitnessRequirements.len(),
                                    SelectedAccessValues.len(),
                                    SelectedAccessValues
                                        .iter()
                                        .map(|Value| Value.OrderedWire.len())
                                        .collect::<Vec<_>>(),
                                    $SourceTerminalVariable,
                                    DetachedSeedAccessPaths.len(),
                                    $SourceDetachedAnchorIndex,
                                    PoweredWitness.as_ref().map(|(Paths, Repeaters)| (
                                        Paths.iter().map(Vec::len).collect::<Vec<_>>(),
                                        Repeaters.len(),
                                    )),
                                );
                            }
                            let PoweredCorridorHint = PoweredWitness.is_some();
                            let mut CertifiedRepeaters = Vec::new();
                            if let Some((CertifiedPaths, RepeaterValues)) = PoweredWitness {
                                if !CertifiedPaths.is_empty() {
                                    DetailedHintPaths = CertifiedPaths;
                                }
                                CertifiedRepeaters = RepeaterValues;
                            }
                            $SharedAccessRampCache
                                .ExhaustivePoweredProofCount
                                .fetch_add(1, Ordering::Relaxed);
                            if PoweredCorridorHint {
                                $SharedAccessRampCache
                                    .KnownPoweredWitnessCount
                                    .fetch_add(1, Ordering::Relaxed);
                            }
                            let AccessWitnessLength = AccessWitnessRequirements
                                .iter()
                                .filter_map(|(Variable, CandidateId)| {
                                    $AccessByVariable.get(Variable).and_then(|Values| {
                                        Values.iter().find(|Value| {
                                            Value.CandidateId == *CandidateId
                                        })
                                    })
                                })
                                .map(|Value| Value.Wire.len())
                                .sum::<usize>();
                            $SignalShapes.push(DeferredGuideCandidateValue {
                                Variable: format!("__route_guide__:{}", $Signal),
                                CandidateId: CandidateId.clone(),
                                OwnerSignal: $Signal.clone(),
                                Requirements: AccessWitnessRequirements.clone(),
                                Portals: PortalTuple
                                    .iter()
                                    .map(|Value| Value.Portal)
                                    .collect(),
                                $RoutingY: *$RoutingY,
                                Axis: Axis.to_string(),
                                Lane,
                                Guide: Guide.clone(),
                                AccessRamps: AccessRamps.clone(),
                                DetailedHintPaths: DetailedHintPaths.clone(),
                                CertifiedRepeaters,
                                PhysicalGuide: PhysicalGuide.clone(),
                                SupportedAccessChoices: (
                                    SupportedAccessChoices.clone()
                                ),
                                CertifiedAccessTuples: Arc::clone(
                                    &AccessWitnessRequirementSets
                                ),
                                $TerminalVariables: $TerminalVariables.clone(),
                                DetachedSeedAccessPaths: DetachedSeedAccessPaths.to_vec(),
                                $SourceTerminalVariable: $SourceTerminalVariable.clone(),
                                $SourceDetachedAnchorIndex: *$SourceDetachedAnchorIndex,
                                PoweredCorridorHint,
                                Claims: Claims.clone(),
                                Priority: (
                                    Guide.len(),
                                    PortalShapeRank,
                                    $LayerIndex,
                                    LaneIndex,
                                    usize::from(Axis != PreferredAxis),
                                    AccessWitnessLength,
                                    Axis.to_string(),
                                    Lane,
                                ),
                            });
                        }
                        }
                    }
                }
            }
    };
}
