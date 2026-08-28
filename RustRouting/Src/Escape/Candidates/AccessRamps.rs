use super::*;

pub(in crate::Escape) fn BuildLayeredGuideNecessaryAccessRamps(
    GraphAdjacency: &HashMap<Position, Vec<Position>>,
    IndexedGraph: &IndexedEscapeGraph,
    ProofCounters: &LayeredGuideAccessRampCache,
    Guide: &[Position],
    GuideExpansion: usize,
    PortalTuple: &[&DeferredAccessCandidateValue],
    BaseClaimIndex: &LayeredFrozenBaseClaimIndex,
    RequiredWire: &HashSet<Position>,
    ForeignBlockedNodes: &HashSet<Position>,
    OwnerSignal: &str,
    DetachedSeedAccessPaths: &[Vec<Position>],
    SourceDetachedAnchorIndex: Option<usize>,
    Deadline: &RuntimeDeadline,
) -> Option<LayeredGuideAccessRampResult> {
    let GuideColumns = Guide
        .iter()
        .map(|(X, _Y, Z)| (*X, *Z))
        .collect::<BTreeSet<_>>();
    let Expansion = GuideExpansion.min(i32::MAX as usize) as i32;
    let mut AllowedColumns = GuideColumns
        .iter()
        .flat_map(|(GuideX, GuideZ)| {
            (-Expansion..=Expansion).flat_map(move |DeltaX| {
                (-Expansion..=Expansion).filter_map(move |DeltaZ| {
                    (DeltaX.abs() + DeltaZ.abs() <= Expansion)
                        .then_some((*GuideX + DeltaX, *GuideZ + DeltaZ))
                })
            })
        })
        .collect::<HashSet<_>>();
    // Fixed internal pin attachments can end inside a component access pocket
    // that is not itself inside the guide corridor.  Retain the complete
    // ordered attachment paths and admit an equally bounded corridor around
    // them so the native catalog owns the exact graph path from the physical
    // pin attachment to the guide.  Only the selected shortest ramp is
    // reserved; this does not turn the expanded corridor into capacity.
    AllowedColumns.extend(DetachedSeedAccessPaths.iter().flatten().flat_map(
        |(PathX, _PathY, PathZ)| {
            (-Expansion..=Expansion).flat_map(move |DeltaX| {
                (-Expansion..=Expansion).filter_map(move |DeltaZ| {
                    (DeltaX.abs() + DeltaZ.abs() <= Expansion)
                        .then_some((*PathX + DeltaX, *PathZ + DeltaZ))
                })
            })
        },
    ));
    // A compact guide factor owns portal endpoints, not one arbitrary full
    // escape path used while enumerating that endpoint.  Exact access paths
    // remain separate variables in the shared claim solver, which rejects
    // every incompatible guide/stub combination.  Treating the enumerator's
    // representative path as mandatory here could discard a guide even when
    // another candidate ending at the same portal was compatible.
    // Necessary connectivity must use the same immutable foreign-claim
    // vocabulary as selected-world routing.  A wire node cannot occupy a
    // foreign electrical, support, or air claim, and its support cannot
    // replace a foreign wire/air block one level below.  Project only these
    // exact unary contradictions here; vertical edge headroom remains part
    // of the later complete claim check because it depends on a node pair.
    // Do not materialize the allowed-node set by scanning the complete graph
    // for every guide candidate.  The guide corridor is already represented
    // by an exact column set, so membership can be tested lazily while the
    // bounded traversal visits nodes.  RCA-sized portfolios contain thousands
    // of guide candidates over the same graph; the old full-graph scan made
    // certification proportional to candidates times every graph node even
    // when only a small corridor was reachable.
    let IsAllowed = |PositionValue: &Position| {
        RequiredWire.contains(PositionValue)
            || (!ForeignBlockedNodes.contains(PositionValue)
                && AllowedColumns.contains(&(PositionValue.0, PositionValue.2)))
    };
    let Terminals = PortalTuple
        .iter()
        .map(|Value| Value.Portal)
        .chain(
            DetachedSeedAccessPaths
                .iter()
                .filter_map(|Path| Path.last().copied()),
        )
        .collect::<BTreeSet<_>>();
    let GuideNodes = Guide
        .iter()
        .copied()
        .filter(|PositionValue| {
            GraphAdjacency.contains_key(PositionValue) && IsAllowed(PositionValue)
        })
        .collect::<HashSet<_>>();
    if Terminals.is_empty()
        || GuideNodes.is_empty()
        || Terminals.iter().any(|PositionValue| {
            !GraphAdjacency.contains_key(PositionValue) || !IsAllowed(PositionValue)
        })
    {
        if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE_SIGNAL")
            .ok()
            .as_deref()
            == Some(OwnerSignal)
        {
            eprintln!(
                "native layered ramp validation signal={} terminals={:?} missing_terminals={:?} guide_nodes={} raw_guide_nodes={}",
                OwnerSignal,
                Terminals,
                Terminals
                    .iter()
                    .filter(|PositionValue| !GraphAdjacency.contains_key(PositionValue))
                    .collect::<Vec<_>>(),
                GuideNodes.len(),
                Guide.len(),
            );
        }
        return Some(None);
    }
    let mut ExpansionCount = 0usize;
    let mut AccessRamps = Vec::new();
    let mut ConnectivityWitnessPaths = Vec::<Vec<Position>>::new();
    let mut PhysicalGuide = GuideNodes.iter().copied().collect::<Vec<_>>();
    PhysicalGuide.sort_unstable();
    let NeedsExactRamp = !PortalTuple.is_empty() && !DetachedSeedAccessPaths.is_empty();
    if !NeedsExactRamp {
        let mut Reached = GuideNodes.clone();
        let mut ParentTowardGuide = HashMap::<Position, Position>::new();
        let mut Pending = VecDeque::from(PhysicalGuide.clone());
        let mut UnreachedTerminals = Terminals
            .iter()
            .copied()
            .filter(|Terminal| !Reached.contains(Terminal))
            .collect::<HashSet<_>>();
        while !UnreachedTerminals.is_empty() {
            let Some(Current) = Pending.pop_front() else {
                return Some(None);
            };
            if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return None;
            }
            ExpansionCount = ExpansionCount.saturating_add(1);
            for Neighbor in GraphAdjacency.get(&Current).into_iter().flatten().copied() {
                if !IsAllowed(&Neighbor) || !Reached.insert(Neighbor) {
                    continue;
                }
                ParentTowardGuide.insert(Neighbor, Current);
                UnreachedTerminals.remove(&Neighbor);
                Pending.push_back(Neighbor);
            }
        }
        for Terminal in Terminals.iter().copied() {
            let mut Current = Terminal;
            let mut WitnessPath = vec![Current];
            while !GuideNodes.contains(&Current) {
                Current = ParentTowardGuide[&Current];
                WitnessPath.push(Current);
            }
            ConnectivityWitnessPaths.push(WitnessPath);
            // Exterior portals own their exact shell path through a separate
            // access-stub variable.  This portal-to-guide traversal is not a
            // capacity reservation, but retaining its complete deterministic
            // BFS witness gives selected-world routing a finite exact subgraph
            // to try before opening the whole expanded corridor.
            AccessRamps.push(vec![Terminal]);
        }
    }
    for Terminal in Terminals.iter().copied() {
        if !NeedsExactRamp {
            continue;
        }
        let mut Reached = HashSet::from([Terminal]);
        let mut Parent = HashMap::<Position, Position>::new();
        let mut Pending = VecDeque::from([Terminal]);
        let mut CandidateGuideNodes = GuideNodes
            .contains(&Terminal)
            .then_some(vec![Terminal])
            .unwrap_or_default();
        let SelectedPath = loop {
            if !CandidateGuideNodes.is_empty() {
                let mut CandidatePaths = CandidateGuideNodes
                    .drain(..)
                    .map(|GuideNode| {
                        let mut Current = GuideNode;
                        let mut Path = vec![Current];
                        while Current != Terminal {
                            Current = Parent[&Current];
                            Path.push(Current);
                        }
                        Path.reverse();
                        Path
                    })
                    .collect::<Vec<_>>();
                // This is the same (length, path) order as the previous
                // complete-corridor enumeration.  Processing one BFS layer at
                // a time lets us stop only after the first self-legal shortest
                // ramp has been proved, without dropping any finite shape.
                CandidatePaths.sort();
                if let Some(Path) = CandidatePaths.into_iter().find(|CandidatePath| {
                    let CombinedWire = PhysicalGuide
                        .iter()
                        .copied()
                        .chain(DetachedSeedAccessPaths.iter().flatten().copied())
                        .chain(AccessRamps.iter().flatten().copied())
                        .chain(CandidatePath.iter().copied())
                        .collect::<BTreeSet<_>>()
                        .into_iter()
                        .collect::<Vec<_>>();
                    let Some(Claims) = BuildDeferredAccessCandidate(
                        format!("__route_guide_bundle__:{}", OwnerSignal),
                        format!("__route_guide_bundle__:{}", OwnerSignal),
                        OwnerSignal.to_string(),
                        Guide.first().map(|Value| Value.1).unwrap_or(Terminal.1),
                        CombinedWire,
                    ) else {
                        return false;
                    };
                    !BaseClaimIndex.Conflicts(&Claims)
                }) {
                    break Some(Path);
                }
            }
            let CurrentLayerSize = Pending.len();
            if CurrentLayerSize == 0 {
                break None;
            }
            let mut NextGuideNodes = Vec::new();
            for _ in 0..CurrentLayerSize {
                let Current = Pending
                    .pop_front()
                    .expect("current BFS layer retains every queued node");
                if ExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return None;
                }
                ExpansionCount = ExpansionCount.saturating_add(1);
                for Neighbor in GraphAdjacency.get(&Current).into_iter().flatten().copied() {
                    if !IsAllowed(&Neighbor) || !Reached.insert(Neighbor) {
                        continue;
                    }
                    Parent.insert(Neighbor, Current);
                    if GuideNodes.contains(&Neighbor) {
                        NextGuideNodes.push(Neighbor);
                    }
                    Pending.push_back(Neighbor);
                }
            }
            CandidateGuideNodes = NextGuideNodes;
        };
        let Some(Path) = SelectedPath else {
            return Some(None);
        };
        ConnectivityWitnessPaths.push(Path.clone());
        AccessRamps.push(Path);
    }
    let mut PoweredCorridorHint = true;
    if PortalTuple.is_empty() && DetachedSeedAccessPaths.len() == 2 {
        PoweredCorridorHint = false;
        let HintWire = PhysicalGuide
            .iter()
            .copied()
            .chain(DetachedSeedAccessPaths.iter().flatten().copied())
            .chain(ConnectivityWitnessPaths.iter().flatten().copied())
            .collect::<BTreeSet<_>>();
        let HintClaims = BuildDeferredAccessCandidate(
            format!("__route_guide_hint__:{OwnerSignal}"),
            format!("__route_guide_hint__:{OwnerSignal}"),
            OwnerSignal.to_string(),
            Guide.first().map(|Value| Value.1).unwrap_or(0),
            HintWire.iter().copied().collect(),
        )
        .filter(|Claims| !BaseClaimIndex.Conflicts(Claims));
        if let Some(SourceDetachedAnchorIndex) = SourceDetachedAnchorIndex
            .filter(|Index| *Index < 2)
            .filter(|_Index| HintClaims.is_some())
        {
            let TargetDetachedAnchorIndex = 1usize - SourceDetachedAnchorIndex;
            if let (Some(SourcePath), Some(TargetIndex)) = (
                DetachedSeedAccessPaths.get(SourceDetachedAnchorIndex),
                DetachedSeedAccessPaths
                    .get(TargetDetachedAnchorIndex)
                    .and_then(|Path| Path.last())
                    .and_then(|Target| IndexedGraph.PositionIndices.get(Target))
                    .copied(),
            ) {
                let mut BestPowerByState = vec![
                    0u8;
                    IndexedGraph
                        .Positions
                        .len()
                        .saturating_mul(ESCAPE_DIRECTION_STATE_COUNT)
                ];
                let mut Pending = VecDeque::new();
                let mut PriorPosition = None;
                let mut PowerRemaining = crate::Path::PathRouting::MAXIMUM_UNREFRESHED_DUST_LENGTH;
                for PositionValue in SourcePath.iter().copied() {
                    let Some(PositionIndex) =
                        IndexedGraph.PositionIndices.get(&PositionValue).copied()
                    else {
                        continue;
                    };
                    let DirectionValue = PriorPosition
                        .map(|Prior: Position| {
                            (
                                PositionValue.0 - Prior.0,
                                PositionValue.1 - Prior.1,
                                PositionValue.2 - Prior.2,
                            )
                        })
                        .unwrap_or((0, 0, 0));
                    let DirectionIndex = EscapeDirectionStateIndex(DirectionValue);
                    let StateIndex = IndexedGraph.StateIndex(PositionIndex, DirectionValue);
                    if PowerRemaining > BestPowerByState[StateIndex] {
                        BestPowerByState[StateIndex] = PowerRemaining;
                        Pending.push_back((PositionIndex, DirectionIndex, PowerRemaining));
                    }
                    PriorPosition = Some(PositionValue);
                    PowerRemaining = PowerRemaining.saturating_sub(1);
                }
                let mut PoweredExpansionCount = 0usize;
                while let Some((CurrentIndex, PriorDirectionIndex, CurrentPower)) =
                    Pending.pop_front()
                {
                    if PoweredExpansionCount % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                        return None;
                    }
                    PoweredExpansionCount = PoweredExpansionCount.saturating_add(1);
                    if CurrentIndex == TargetIndex {
                        PoweredCorridorHint = true;
                        break;
                    }
                    let Current = IndexedGraph.Positions[CurrentIndex];
                    for NextIndex in IndexedGraph.NeighborIndices[CurrentIndex].iter().copied() {
                        let Next = IndexedGraph.Positions[NextIndex];
                        if !HintWire.contains(&Next) {
                            continue;
                        }
                        let DirectionValue =
                            (Next.0 - Current.0, Next.1 - Current.1, Next.2 - Current.2);
                        let DirectionIndex = EscapeDirectionStateIndex(DirectionValue);
                        let NextPower = if PriorDirectionIndex != ESCAPE_INITIAL_DIRECTION_STATE
                            && PriorDirectionIndex == DirectionIndex
                            && (1..=4).contains(&DirectionIndex)
                        {
                            crate::Path::PathRouting::MAXIMUM_UNREFRESHED_DUST_LENGTH
                        } else {
                            CurrentPower.saturating_sub(1)
                        };
                        if NextPower == 0 {
                            continue;
                        }
                        let NextStateIndex = IndexedGraph.StateIndex(NextIndex, DirectionValue);
                        if NextPower <= BestPowerByState[NextStateIndex] {
                            continue;
                        }
                        BestPowerByState[NextStateIndex] = NextPower;
                        Pending.push_back((NextIndex, DirectionIndex, NextPower));
                    }
                }
                ProofCounters
                    .ExhaustivePoweredProofCount
                    .fetch_add(1, Ordering::Relaxed);
                if PoweredCorridorHint {
                    ProofCounters
                        .KnownPoweredWitnessCount
                        .fetch_add(1, Ordering::Relaxed);
                } else if std::env::var("RCS_DEBUG_NATIVE_ACCESS_GUIDE_SIGNAL")
                    .ok()
                    .as_deref()
                    == Some(OwnerSignal)
                {
                    eprintln!(
                        "native layered fixed-factor powered hint missing signal={} expansions={}",
                        OwnerSignal, PoweredExpansionCount,
                    );
                }
            }
        }
    }
    Some(Some((
        PhysicalGuide,
        AccessRamps,
        ConnectivityWitnessPaths,
        PoweredCorridorHint,
    )))
}

pub(in crate::Escape) fn LayeredGuideControlsHaveFixedBaseConflict(
    Controls: &LayeredAccessGuideControlsValue,
) -> bool {
    let BaseValues = Controls
        .10
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
    BaseValues.iter().enumerate().any(|(FirstIndex, First)| {
        BaseValues
            .iter()
            .skip(FirstIndex + 1)
            .any(|Second| DeferredAccessCandidatesConflict(First, Second))
    })
}
