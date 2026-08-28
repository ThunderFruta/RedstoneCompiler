use super::*;

pub(in crate::Escape) fn BuildLayeredAccessGuideCandidateGroups(
    Requests: &[EscapeRequest],
    RequestResults: &[EscapeRequestResult],
    RequestMetadata: &[(String, String, String)],
    Controls: &LayeredAccessGuideControlsValue,
    GraphAdjacencyValues: &[(Position, Vec<Position>)],
    MemberIndex: usize,
    GraphIndex: usize,
    MaximumY: i32,
    RequirePowerCertifiedAccess: bool,
    SharedAccessRampCache: &LayeredGuideAccessRampCache,
    Deadline: &RuntimeDeadline,
) -> PyResult<Option<PreparedLayeredAccessGuideDomain>> {
    let DomainStartedAt = Instant::now();
    let mut DebugStageStartedAt = Instant::now();
    let Some((RequiredVariables, AllAccessValues)) =
        BuildDeferredLayeredAccessCandidates(Requests, RequestResults, RequestMetadata, Deadline)?
    else {
        return Ok(None);
    };
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered domain stage=deferred-access values={} elapsed={:.3}s",
            AllAccessValues.len(),
            DebugStageStartedAt.elapsed().as_secs_f64(),
        );
    }
    DebugStageStartedAt = Instant::now();
    let (
        RoutingYs,
        MinimumX,
        MinimumZ,
        TrackPitch,
        LaneCount,
        MaximumShapesPerSignal,
        GuideExpansion,
        RegionExpansion,
        FabricNodeCandidateValues,
        SignalValues,
        BaseClaimValues,
        DetachedSeedAnchorValues,
    ) = Controls;
    if RoutingYs.is_empty() || *TrackPitch < 2 || *LaneCount < 1 || *MaximumShapesPerSignal < 1 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access guide controls require routing planes, pitch, lanes, and shapes",
        ));
    }
    let SourceAccessVariables = SignalValues
        .iter()
        .filter_map(
            |(
                _Signal,
                _TerminalVariables,
                _VariantCount,
                _RegionTerminals,
                SourceTerminalVariable,
                _SourceDetachedAnchorIndex,
            )| SourceTerminalVariable.clone(),
        )
        .collect::<HashSet<_>>();
    let AccessValues = if RequirePowerCertifiedAccess {
        AllAccessValues
            .into_iter()
            .filter(|Access| {
                ExactLayeredAccessPathCanCarryPower(
                    SourceAccessVariables.contains(&Access.Variable),
                    &Access.OrderedWire,
                )
            })
            .collect::<Vec<_>>()
    } else {
        AllAccessValues
    };
    let BaseValues = BaseClaimValues
        .iter()
        .enumerate()
        .map(|(Index, (Signal, Wire, Support, Air, Electrical))| {
            let Sorted = |Values: &[Position]| {
                Values
                    .iter()
                    .copied()
                    .collect::<BTreeSet<_>>()
                    .into_iter()
                    .collect()
            };
            DeferredAccessCandidateValue {
                Variable: format!("__base_claim__:{}:{}", Signal, Index),
                CandidateId: format!("__base_claim_value__:{}:{}", Signal, Index),
                OwnerSignal: Signal.clone(),
                IngressY: 0,
                Portal: Wire.first().copied().unwrap_or((0, 0, 0)),
                OrderedWire: Wire.to_vec(),
                Wire: Sorted(Wire),
                Support: Sorted(Support),
                Air: Sorted(Air),
                Electrical: Sorted(Electrical),
            }
        })
        .collect::<Vec<_>>();
    let BaseValuesByOwner = BaseValues.iter().fold(
        HashMap::<String, Vec<&DeferredAccessCandidateValue>>::new(),
        |mut Result, Value| {
            Result
                .entry(Value.OwnerSignal.clone())
                .or_default()
                .push(Value);
            Result
        },
    );
    let RequiredWireByOwner = SignalValues
        .iter()
        .map(
            |(
                Signal,
                _TerminalVariables,
                _PortalVariantLimit,
                _RegionTerminals,
                _SourceTerminalVariable,
                _SourceDetachedAnchorIndex,
            )| {
                let RequiredWire = BaseValuesByOwner
                    .get(Signal)
                    .into_iter()
                    .flatten()
                    .flat_map(|Value| Value.Wire.iter().copied())
                    .collect::<HashSet<_>>();
                (Signal.clone(), RequiredWire)
            },
        )
        .collect::<HashMap<_, _>>();
    let ForeignBlockedNodesByOwner = SignalValues
        .iter()
        .map(
            |(
                Signal,
                _TerminalVariables,
                _PortalVariantLimit,
                _RegionTerminals,
                _SourceTerminalVariable,
                _SourceDetachedAnchorIndex,
            )| {
                let ForeignBlockedNodes = BaseValues
                    .iter()
                    .filter(|Value| Value.OwnerSignal != *Signal)
                    .flat_map(|Value| {
                        Value
                            .Electrical
                            .iter()
                            .chain(&Value.Support)
                            .chain(&Value.Air)
                            .copied()
                            .chain(Value.Wire.iter().chain(&Value.Air).map(|PositionValue| {
                                (PositionValue.0, PositionValue.1 + 1, PositionValue.2)
                            }))
                    })
                    .collect::<HashSet<_>>();
                (Signal.clone(), ForeignBlockedNodes)
            },
        )
        .collect::<HashMap<_, _>>();
    if BaseValues.iter().enumerate().any(|(FirstIndex, First)| {
        BaseValues
            .iter()
            .skip(FirstIndex + 1)
            .any(|Second| DeferredAccessCandidatesConflict(First, Second))
    }) {
        // Base claims are already frozen into the selected placement. They
        // are constraints, not assignment choices. A contradictory fixed
        // base makes this non-exhaustive member incomplete without exposing
        // the claims as synthetic singleton variables to the solver.
        return Ok(Some((
            BTreeMap::from([("__fixed_base_claim_conflict__".to_string(), Vec::new())]),
            1,
            HashMap::new(),
            Arc::new(vec![Vec::new()]),
        )));
    }
    let BaseClaimIndex = Arc::new(LayeredFrozenBaseClaimIndex::New(&BaseValues));
    // Frozen placement-owned claims are part of the same physical world as
    // every access path.  Reject an access value that contradicts them before
    // it can become either an assignment value or a guide witness.  The old
    // guide-only check allowed native selection to choose a stub whose merged
    // selected-world claims were rejected during Python handoff.
    let AccessValues = AccessValues
        .into_iter()
        .filter(|Access| !BaseClaimIndex.Conflicts(Access))
        .collect::<Vec<_>>();
    // Guide requirements and access variables must refer to the same exact
    // finite physical domain.  Powered alternatives are distinct paths, not
    // compatibility-only witnesses, and subset dominance is unsound because
    // a longer path can provide the only legal repeater sites.
    let GuideAccessValues = AccessValues.clone();
    let AccessByVariable = GuideAccessValues.iter().fold(
        BTreeMap::<String, Vec<&DeferredAccessCandidateValue>>::new(),
        |mut Result, Value| {
            Result
                .entry(Value.Variable.clone())
                .or_default()
                .push(Value);
            Result
        },
    );
    let CompleteAccessWitness = Arc::new(FindCompleteLayeredAccessWitness(
        &AccessByVariable,
        Deadline,
    ));
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered domain stage=access-base-witness values={} base_values={} elapsed={:.3}s",
            GuideAccessValues.len(),
            BaseValues.len(),
            DebugStageStartedAt.elapsed().as_secs_f64(),
        );
    }
    DebugStageStartedAt = Instant::now();
    let DetachedSeedAnchorsByOwner = DetachedSeedAnchorValues
        .iter()
        .map(|(Signal, Anchors)| (Signal.as_str(), Anchors.as_slice()))
        .collect::<HashMap<_, _>>();
    let GraphAdjacency = Arc::new(
        GraphAdjacencyValues
            .iter()
            .filter(|(PositionValue, _Neighbors)| PositionValue.1 <= MaximumY)
            .map(|(PositionValue, Neighbors)| {
                (
                    *PositionValue,
                    Neighbors
                        .iter()
                        .copied()
                        .filter(|Neighbor| Neighbor.1 <= MaximumY)
                        .collect::<Vec<_>>(),
                )
            })
            .collect::<HashMap<_, _>>(),
    );
    let IndexedGraph = Arc::new(IndexedEscapeGraph::New(&GraphAdjacency));
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered domain stage=indexed-graph nodes={} elapsed={:.3}s",
            GraphAdjacency.len(),
            DebugStageStartedAt.elapsed().as_secs_f64(),
        );
    }
    DebugStageStartedAt = Instant::now();
    let mut MemberAssignedColumns = FabricNodeCandidateValues
        .iter()
        .copied()
        .filter(|PositionValue| GraphAdjacency.contains_key(PositionValue))
        .map(|PositionValue| (PositionValue.0, PositionValue.2))
        .collect::<HashSet<_>>();
    let AccessAssignedColumns = GuideAccessValues
        .iter()
        .flat_map(|Value| Value.Wire.iter())
        .map(|PositionValue| (PositionValue.0, PositionValue.2))
        .collect::<HashSet<_>>();
    MemberAssignedColumns.extend(AccessAssignedColumns.iter().copied());
    let Expansion = (*RegionExpansion).min(i32::MAX as usize) as i32;
    for (
        _Signal,
        _TerminalVariables,
        _PortalVariantLimit,
        RegionTerminals,
        _SourceTerminalVariable,
        _SourceDetachedAnchorIndex,
    ) in SignalValues
    {
        let Terminals = RegionTerminals
            .iter()
            .copied()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        if Terminals.is_empty() {
            continue;
        }
        for Axis in ["X", "Z"] {
            let mut Coordinates = Terminals
                .iter()
                .map(|Value| if Axis == "X" { Value.1 } else { Value.0 })
                .collect::<Vec<_>>();
            Coordinates.sort_unstable();
            let Center = Coordinates[Coordinates.len() / 2];
            let TrackAnchor = if Axis == "X" { *MinimumZ } else { *MinimumX };
            let Pitch = *TrackPitch as i32;
            let AlignedCenter =
                TrackAnchor + (Center - TrackAnchor + Pitch / 2).div_euclid(Pitch) * Pitch;
            let Guide = BuildLayeredGuideSpine(&Terminals, Axis, AlignedCenter, RoutingYs[0]);
            MemberAssignedColumns.extend(Guide.iter().flat_map(|(GuideX, _GuideY, GuideZ)| {
                (-Expansion..=Expansion).flat_map(move |DeltaX| {
                    (-Expansion..=Expansion).filter_map(move |DeltaZ| {
                        (DeltaX.abs() + DeltaZ.abs() <= Expansion)
                            .then_some((*GuideX + DeltaX, *GuideZ + DeltaZ))
                    })
                })
            }));
        }
    }
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered domain stage=assigned-columns columns={} elapsed={:.3}s",
            MemberAssignedColumns.len(),
            DebugStageStartedAt.elapsed().as_secs_f64(),
        );
    }
    let mut SignalOrder = (0..SignalValues.len()).collect::<Vec<_>>();
    SignalOrder.sort_by_key(|SignalIndex| {
        let (
            Signal,
            TerminalVariables,
            PortalVariantLimit,
            _RegionTerminals,
            _SourceTerminalVariable,
            _SourceDetachedAnchorIndex,
        ) = &SignalValues[*SignalIndex];
        (
            Reverse(TerminalVariables.len()),
            Reverse(LayeredGuideTerminalSpan(TerminalVariables)),
            *PortalVariantLimit,
            Signal.clone(),
            *SignalIndex,
        )
    });
    let FirstEmptySignalOrderIndex = std::sync::atomic::AtomicUsize::new(usize::MAX);
    let GuideValuesBySignal =
        RoutingThreadPool().install(|| {
            SignalOrder
            .par_iter()
            .enumerate()
            .with_max_len(1)
            .map(|(OrderIndex, SignalIndex)| -> PyResult<Option<Vec<DeferredGuideCandidateValue>>> {
        if OrderIndex
            > FirstEmptySignalOrderIndex.load(std::sync::atomic::Ordering::SeqCst)
        {
            return Ok(Some(Vec::new()));
        }
        let SignalIndex = *SignalIndex;
        let (
            Signal,
            TerminalVariables,
            PortalVariantLimit,
            _RegionTerminals,
            SourceTerminalVariable,
            SourceDetachedAnchorIndex,
        ) =
            &SignalValues[SignalIndex];
        let SignalStartedAt = Instant::now();
        let mut PoweredWitnessWorkspace = LayeredPoweredWitnessWorkspace::New(&IndexedGraph);
        if SignalIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Ok(None);
        }
        let DetachedSeedAnchors = DetachedSeedAnchorsByOwner
            .get(Signal.as_str())
            .copied()
            .unwrap_or(&[]);
        if Signal.is_empty()
            || TerminalVariables.len() + DetachedSeedAnchors.len() < 2
            || *PortalVariantLimit < 1
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "layered guide signals require an owner, source, target, and portal limit",
            ));
        }
        if TerminalVariables
            .iter()
            .any(|Variable| !RequiredVariables.contains_key(Variable))
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "layered guide signal references an unknown access variable",
            ));
        }
        let mut SignalShapes = Vec::<DeferredGuideCandidateValue>::new();
        let mut ClaimBundleRejectionCount = 0usize;
        let mut AccessRampConnectivityRejectionCount = 0usize;
        let mut AccessRampSelfConflictRejectionCount = 0usize;
        let mut AccessRampBaseConflictRejectionCount = 0usize;
        let mut PoweredTreeRejectionCount = 0usize;
        let mut AccessWitnessExpansionCount = 0usize;
        let mut AccessWitnessByPhysicalGuide = HashMap::<
            (usize, String, i32, Vec<Position>),
            (
                Arc<Vec<Vec<(String, String)>>>,
                BTreeSet<(String, String)>,
            ),
        >::new();
        let mut AccessRampsByPhysicalGuide = HashMap::<
            (usize, String, i32, Vec<Position>),
            LayeredGuideAccessRampResult,
        >::new();
        let mut PreferredAccessWitnessByPortalTuple =
            HashMap::<Vec<Position>, Vec<Vec<(String, String)>>>::new();
        EnumerateLayeredGuideShapesPhase!(
                    TerminalVariables,
                    AccessByVariable,
                    CompleteAccessWitness,
                    RequiredVariables,
                    PortalVariantLimit,
                    Deadline,
                    RoutingYs,
                    DetachedSeedAnchorsByOwner,
                    Signal,
                    MinimumZ,
                    MinimumX,
                    TrackPitch,
                    LaneCount,
                    MaximumShapesPerSignal,
                    BaseValuesByOwner,
                    RequiredWireByOwner,
                    ForeignBlockedNodesByOwner,
                    AccessRampsByPhysicalGuide,
                    MemberIndex,
                    GuideExpansion,
                    GraphAdjacency,
                    IndexedGraph,
                    SharedAccessRampCache,
                    BaseClaimIndex,
                    SourceDetachedAnchorIndex,
                    PreferredAccessWitnessByPortalTuple,
                    AccessWitnessByPhysicalGuide,
                    ClaimBundleRejectionCount,
                    AccessRampConnectivityRejectionCount,
                    AccessRampSelfConflictRejectionCount,
                    AccessRampBaseConflictRejectionCount,
                    AccessWitnessExpansionCount,
                    PoweredWitnessWorkspace,
                    SourceTerminalVariable,
                    SignalShapes
                );
        if SignalShapes.is_empty()
            && std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some()
        {
            eprintln!(
                "native layered guide empty signal={} claim_bundle_rejections={} ramp_connectivity_rejections={} ramp_self_conflict_rejections={} ramp_base_conflict_rejections={} powered_tree_rejections={}",
                Signal,
                ClaimBundleRejectionCount,
                AccessRampConnectivityRejectionCount,
                AccessRampSelfConflictRejectionCount,
                AccessRampBaseConflictRejectionCount,
                PoweredTreeRejectionCount,
            );
        }
        SignalShapes.sort_by(|First, Second| {
            usize::from(!First.PoweredCorridorHint)
                .cmp(&usize::from(!Second.PoweredCorridorHint))
                .then_with(|| First.Priority.cmp(&Second.Priority))
                .then_with(|| First.CandidateId.cmp(&Second.CandidateId))
        });
        if std::env::var("RCS_DEBUG_LAYERED_POWERED_SIGNAL")
            .ok()
            .is_some_and(|DebugSignal| DebugSignal == *Signal)
        {
            eprintln!(
                "native layered signal candidate order signal={} terminals={:?} source_terminal={:?} detached_count={} source_detached={:?} total={} powered={} candidates={:?}",
                Signal,
                TerminalVariables,
                SourceTerminalVariable,
                DetachedSeedAnchors.len(),
                SourceDetachedAnchorIndex,
                SignalShapes.len(),
                SignalShapes.iter().filter(|Value| Value.PoweredCorridorHint).count(),
                SignalShapes
                    .iter()
                    .take(32)
                    .map(|Value| (
                        Value.CandidateId.as_str(),
                        Value.RoutingY,
                        Value.Axis.as_str(),
                        Value.Lane,
                        Value.PoweredCorridorHint,
                        &Value.Priority,
                        Value.Guide.first(),
                        Value.Guide.last(),
                    ))
                    .collect::<Vec<_>>(),
            );
        }
        SignalShapes.dedup_by(|First, Second| {
            First.RoutingY == Second.RoutingY
                && First.Axis == Second.Axis
                && First.Lane == Second.Lane
                && First.Guide == Second.Guide
                && First.Portals == Second.Portals
                && First.Requirements == Second.Requirements
        });
        let PhysicalShapeIdsByRoutingY = SignalShapes.iter().fold(
            BTreeMap::<i32, Vec<String>>::new(),
            |mut Result, Value| {
                let ShapeId = Value
                    .CandidateId
                    .split_once(":access:")
                    .map_or(Value.CandidateId.as_str(), |(Base, _Suffix)| Base);
                let Values = Result.entry(Value.RoutingY).or_default();
                if !Values.iter().any(|Existing| Existing == ShapeId) {
                    Values.push(ShapeId.to_string());
                }
                Result
            },
        );
        let mut RetainedPhysicalShapeIds = HashSet::<String>::new();
        let mut LayerOffset = 0usize;
        while RetainedPhysicalShapeIds.len() < *MaximumShapesPerSignal {
            let mut Added = false;
            for Values in PhysicalShapeIdsByRoutingY.values() {
                let Some(ShapeId) = Values.get(LayerOffset) else {
                    continue;
                };
                RetainedPhysicalShapeIds.insert(ShapeId.clone());
                Added = true;
                if RetainedPhysicalShapeIds.len() >= *MaximumShapesPerSignal {
                    break;
                }
            }
            if !Added {
                break;
            }
            LayerOffset += 1;
        }
        SignalShapes.retain(|Value| {
            let ShapeId = Value
                .CandidateId
                .split_once(":access:")
                .map_or(Value.CandidateId.as_str(), |(Base, _Suffix)| Base);
            RetainedPhysicalShapeIds.contains(ShapeId)
        });
        if SignalShapes.is_empty() {
            FirstEmptySignalOrderIndex.fetch_min(
                OrderIndex,
                std::sync::atomic::Ordering::SeqCst,
            );
        }
        if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
            eprintln!(
                    "native layered signal factors signal={} shapes={} witness_expansions={} elapsed={:.3}s",
                    Signal,
                    SignalShapes.len(),
                    AccessWitnessExpansionCount,
                    SignalStartedAt.elapsed().as_secs_f64(),
                );
        }
        Ok(Some(SignalShapes))
            })
            .collect::<Vec<_>>()
        });
    let FirstEmptySignalOrderIndex =
        FirstEmptySignalOrderIndex.load(std::sync::atomic::Ordering::SeqCst);
    if FirstEmptySignalOrderIndex != usize::MAX {
        let SignalIndex = SignalOrder[FirstEmptySignalOrderIndex];
        let (
            Signal,
            _TerminalVariables,
            _PortalVariantLimit,
            _RegionTerminals,
            _SourceTerminalVariable,
            _SourceDetachedAnchorIndex,
        ) = &SignalValues[SignalIndex];
        return Ok(Some((
            BTreeMap::from([(format!("__route_guide__:{Signal}"), Vec::new())]),
            1,
            HashMap::new(),
            Arc::new(vec![Vec::new()]),
        )));
    }
    let mut GuideValues = Vec::<DeferredGuideCandidateValue>::new();
    for (SignalIndex, Outcome) in SignalOrder.iter().zip(GuideValuesBySignal) {
        let (
            _Signal,
            _TerminalVariables,
            _PortalVariantLimit,
            _RegionTerminals,
            _SourceTerminalVariable,
            _SourceDetachedAnchorIndex,
        ) = &SignalValues[*SignalIndex];
        let Some(mut SignalShapes) = Outcome? else {
            return Ok(None);
        };
        GuideValues.append(&mut SignalShapes);
    }
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered guide factor extraction guides={} elapsed={:.3}s",
            GuideValues.len(),
            DomainStartedAt.elapsed().as_secs_f64(),
        );
    }
    let CandidateWirePositions = AccessValues
        .iter()
        .flat_map(|Value| Value.Wire.iter().copied())
        .chain(
            GuideValues
                .iter()
                .flat_map(|Value| Value.Claims.Wire.iter().copied()),
        )
        .collect::<HashSet<_>>();
    let mut ResourcePositions = AccessValues
        .iter()
        .flat_map(|Value| {
            Value
                .Wire
                .iter()
                .chain(&Value.Support)
                .chain(&Value.Air)
                .chain(&Value.Electrical)
                .copied()
        })
        .chain(GuideValues.iter().flat_map(|Value| {
            Value
                .Claims
                .Wire
                .iter()
                .chain(&Value.Claims.Support)
                .chain(&Value.Claims.Air)
                .chain(&Value.Claims.Electrical)
                .copied()
        }))
        .collect::<BTreeSet<_>>();
    for First in &CandidateWirePositions {
        for Second in AccessNeighborPositions(*First) {
            if Second.1 == First.1 || !CandidateWirePositions.contains(&Second) {
                continue;
            }
            let Lower = if First.1 < Second.1 { *First } else { Second };
            ResourcePositions.insert((Lower.0, Lower.1 + 1, Lower.2));
        }
    }
    let ResourcePositions = ResourcePositions.into_iter().collect::<Vec<_>>();
    let ResourceIndex = ResourcePositions
        .iter()
        .copied()
        .enumerate()
        .map(|(Index, PositionValue)| (PositionValue, Index))
        .collect::<HashMap<_, _>>();
    let ResourceCount = ResourceIndex.len().max(1);
    let mut CrossAirByWire = vec![Vec::<(usize, usize)>::new(); ResourceCount];
    for First in &CandidateWirePositions {
        let Some(FirstIndex) = ResourceIndex.get(First).copied() else {
            continue;
        };
        for Second in AccessNeighborPositions(*First) {
            if Second.1 == First.1 || !CandidateWirePositions.contains(&Second) {
                continue;
            }
            let Some(SecondIndex) = ResourceIndex.get(&Second).copied() else {
                continue;
            };
            let Lower = if First.1 < Second.1 { *First } else { Second };
            let AirPosition = (Lower.0, Lower.1 + 1, Lower.2);
            let Some(AirIndex) = ResourceIndex.get(&AirPosition).copied() else {
                continue;
            };
            CrossAirByWire[FirstIndex].push((SecondIndex, AirIndex));
        }
    }
    for Values in &mut CrossAirByWire {
        Values.sort_unstable();
        Values.dedup();
    }
    let mut Groups = RequiredVariables
        .keys()
        .map(|Variable| (Variable.clone(), Vec::new()))
        .collect::<BTreeMap<_, _>>();
    for (
        Signal,
        _TerminalVariables,
        _VariantCount,
        _RegionTerminals,
        _SourceTerminalVariable,
        _SourceDetachedAnchorIndex,
    ) in SignalValues
    {
        Groups
            .entry(format!("__route_guide__:{}", Signal))
            .or_default();
    }
    let BuildClaims = |Value: &DeferredAccessCandidateValue| {
        let Remap = |Positions: &[Position]| {
            Positions
                .iter()
                .map(|PositionValue| ResourceIndex[PositionValue])
                .collect::<Vec<_>>()
        };
        ClaimMask::FromIndicesWithDeadline(
            ResourceCount,
            &Remap(&Value.Wire),
            &Remap(&Value.Support),
            &Remap(&Value.Air),
            &Remap(&Value.Electrical),
            Deadline,
        )
    };
    let AccessValueByChoice = GuideAccessValues
        .iter()
        .map(|Value| {
            (
                (Value.Variable.clone(), Value.CandidateId.clone()),
                Value.Portal,
            )
        })
        .collect::<HashMap<_, _>>();
    for Value in AccessValues {
        let Claims = match BuildClaims(&Value) {
            Ok(Value) => Arc::new(Value),
            Err(ClaimMaskBuildError::DeadlineExceeded) => return Ok(None),
            Err(ClaimMaskBuildError::IndexOutOfRange) => unreachable!(),
        };
        let LogicalKey = Value
            .Variable
            .strip_prefix("__access_terminal__:")
            .expect("validated layered access variable");
        let Contract = format!(
            "access-stub:{}={};access-portal:{}={}",
            LogicalKey,
            Value.CandidateId,
            LogicalKey,
            LayeredAccessPortalContractValue(Value.Portal),
        );
        Groups
            .get_mut(&Value.Variable)
            .unwrap()
            .push(AssignmentCandidate {
                CandidateId: Value.CandidateId,
                OwnerSignal: Value.OwnerSignal,
                TemplateRequirements: ParseContractRequirements(&Contract),
                ForbiddenCandidateIds: Arc::new(Vec::new()),
                OrderedWire: Arc::new(Value.OrderedWire),
                PoweredAccessConstraint: None,
                Claims,
                MaterialCost: 0,
                FootprintGrowth: 0,
                Length: Value.Wire.len().min(i32::MAX as usize) as i32,
                BendCount: 0,
                ViaCount: 0,
            });
    }
    let mut GuideRecipes = HashMap::new();
    for Value in GuideValues {
        let PortalByAccessVariable = Value
            .Requirements
            .iter()
            .map(|(Variable, CandidateId)| {
                (
                    Variable.clone(),
                    AccessValueByChoice[&(Variable.clone(), CandidateId.clone())],
                )
            })
            .collect::<HashMap<_, _>>();
        let mut ForbiddenCandidateIds = GuideAccessValues
            .iter()
            .filter(|Access| {
                PortalByAccessVariable
                    .get(&Access.Variable)
                    .is_some_and(|Portal| *Portal == Access.Portal)
                    && !Value
                        .SupportedAccessChoices
                        .contains(&(Access.Variable.clone(), Access.CandidateId.clone()))
            })
            .map(|Access| (Access.Variable.clone(), Access.CandidateId.clone()))
            .collect::<Vec<_>>();
        ForbiddenCandidateIds.sort();
        ForbiddenCandidateIds.dedup();
        let Claims = match BuildClaims(&Value.Claims) {
            Ok(Value) => Arc::new(Value),
            Err(ClaimMaskBuildError::DeadlineExceeded) => return Ok(None),
            Err(ClaimMaskBuildError::IndexOutOfRange) => unreachable!(),
        };
        let PoweredWitnessWire = Value
            .Claims
            .OrderedWire
            .iter()
            .copied()
            .chain(Value.DetailedHintPaths.iter().flatten().copied())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>();
        let Contract = BuildLayeredGuideAccessContract(&Value.Requirements, &AccessValueByChoice);
        GuideRecipes.insert(
            Value.CandidateId.clone(),
            (
                Value.Variable.clone(),
                Value.CandidateId.clone(),
                Value.Requirements.clone(),
                Value.RoutingY,
                Value.Axis.clone(),
                Value.Lane,
                Value.Guide.clone(),
                Value.AccessRamps.clone(),
                Value.PhysicalGuide.clone(),
                Value.DetailedHintPaths.clone(),
                Value.CertifiedRepeaters.clone(),
            ),
        );
        Groups
            .get_mut(&Value.Variable)
            .unwrap()
            .push(AssignmentCandidate {
                CandidateId: Value.CandidateId,
                OwnerSignal: Value.OwnerSignal,
                TemplateRequirements: ParseContractRequirements(&Contract),
                ForbiddenCandidateIds: Arc::new(ForbiddenCandidateIds),
                OrderedWire: Arc::new(PoweredWitnessWire),
                PoweredAccessConstraint: Some(Arc::new(AssignmentPoweredAccessConstraint {
                    HasPoweredTreeWitness: Value.PoweredCorridorHint,
                    GraphAdjacency: Arc::clone(&GraphAdjacency),
                    TerminalVariables: Arc::new(Value.TerminalVariables.clone()),
                    DetachedSeedAccessPaths: Arc::new(Value.DetachedSeedAccessPaths.clone()),
                    SourceTerminalVariable: Value.SourceTerminalVariable.clone(),
                    SourceDetachedAnchorIndex: Value.SourceDetachedAnchorIndex,
                    PreferredAccessCandidateTuples: Arc::clone(&Value.CertifiedAccessTuples),
                })),
                Claims,
                // Preserve the shared guide enumerator's physical preference
                // order in the native assignment objective.  Re-sorting only
                // by guide length and the opaque candidate id made equal-length
                // lanes choose by hash identity, which could turn a compact
                // feasible witness into an unnecessarily remote detailed lane.
                MaterialCost: Value
                    .Priority
                    .2
                    .saturating_add(usize::from(!Value.PoweredCorridorHint).saturating_mul(16))
                    .min(i32::MAX as usize) as i32,
                FootprintGrowth: Value.Priority.1.min(i32::MAX as usize) as i32,
                Length: Value.Priority.0.min(i32::MAX as usize) as i32,
                BendCount: Value.Priority.3.min(i32::MAX as usize) as i32,
                ViaCount: Value.Priority.4.min(i32::MAX as usize) as i32,
            });
    }
    Ok(Some((
        Groups,
        ResourceCount,
        GuideRecipes,
        Arc::new(CrossAirByWire),
    )))
}
