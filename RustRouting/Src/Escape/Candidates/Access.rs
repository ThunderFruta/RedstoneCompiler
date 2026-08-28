use super::*;

#[derive(Clone)]
pub(in crate::Escape) struct DeferredAccessCandidateValue {
    pub(in crate::Escape) Variable: String,
    pub(in crate::Escape) CandidateId: String,
    pub(in crate::Escape) OwnerSignal: String,
    pub(in crate::Escape) IngressY: i32,
    pub(in crate::Escape) Portal: Position,
    pub(in crate::Escape) OrderedWire: Vec<Position>,
    pub(in crate::Escape) Wire: Vec<Position>,
    pub(in crate::Escape) Support: Vec<Position>,
    pub(in crate::Escape) Air: Vec<Position>,
    pub(in crate::Escape) Electrical: Vec<Position>,
}

#[derive(Clone, Eq, Hash, PartialEq)]
pub(in crate::Escape) struct LayeredGuideAccessRampCacheKey {
    pub(in crate::Escape) MemberIndex: usize,
    pub(in crate::Escape) LayerIndex: usize,
    pub(in crate::Escape) Axis: String,
    pub(in crate::Escape) Lane: i32,
    pub(in crate::Escape) PortalIdentity: Vec<Position>,
    pub(in crate::Escape) Guide: Vec<Position>,
    pub(in crate::Escape) GuideExpansion: usize,
    pub(in crate::Escape) RequiredWire: Vec<Position>,
    pub(in crate::Escape) ForeignBlockedNodes: Vec<Position>,
    pub(in crate::Escape) OwnerSignal: String,
    pub(in crate::Escape) DetachedSeedAccessPaths: Vec<Vec<Position>>,
    pub(in crate::Escape) SourceDetachedAnchorIndex: Option<usize>,
}

pub(in crate::Escape) type LayeredGuideAccessRampResult =
    Option<(Vec<Position>, Vec<Vec<Position>>, Vec<Vec<Position>>, bool)>;
pub(in crate::Escape) struct LayeredGuideAccessRampCache {
    pub(in crate::Escape) Values: Mutex<
        HashMap<
            LayeredGuideAccessRampCacheKey,
            Arc<OnceLock<Option<LayeredGuideAccessRampResult>>>,
        >,
    >,
    pub(in crate::Escape) HitCount: AtomicUsize,
    pub(in crate::Escape) MissCount: AtomicUsize,
    pub(in crate::Escape) KnownPoweredWitnessCount: AtomicUsize,
    pub(in crate::Escape) ExhaustivePoweredProofCount: AtomicUsize,
}

impl LayeredGuideAccessRampCache {
    pub(in crate::Escape) fn New() -> Self {
        Self {
            Values: Mutex::new(HashMap::new()),
            HitCount: AtomicUsize::new(0),
            MissCount: AtomicUsize::new(0),
            KnownPoweredWitnessCount: AtomicUsize::new(0),
            ExhaustivePoweredProofCount: AtomicUsize::new(0),
        }
    }

    pub(in crate::Escape) fn GetCell(
        &self,
        Key: LayeredGuideAccessRampCacheKey,
    ) -> Arc<OnceLock<Option<LayeredGuideAccessRampResult>>> {
        let mut Values = self
            .Values
            .lock()
            .expect("layered guide access-ramp cache lock is not poisoned");
        if let Some(Value) = Values.get(&Key) {
            self.HitCount.fetch_add(1, Ordering::Relaxed);
            return Value.clone();
        }
        self.MissCount.fetch_add(1, Ordering::Relaxed);
        let Value = Arc::new(OnceLock::new());
        Values.insert(Key, Value.clone());
        Value
    }

    pub(in crate::Escape) fn Counts(&self) -> (usize, usize, usize, usize, usize) {
        (
            self.HitCount.load(Ordering::Relaxed),
            self.MissCount.load(Ordering::Relaxed),
            self.KnownPoweredWitnessCount.load(Ordering::Relaxed),
            self.ExhaustivePoweredProofCount.load(Ordering::Relaxed),
            self.Values
                .lock()
                .expect("layered guide access-ramp cache lock is not poisoned")
                .len(),
        )
    }
}

pub(in crate::Escape) fn AccessNeighborPositions((X, Y, Z): Position) -> [Position; 12] {
    [
        (X + 1, Y, Z),
        (X - 1, Y, Z),
        (X, Y, Z + 1),
        (X, Y, Z - 1),
        (X + 1, Y + 1, Z),
        (X + 1, Y - 1, Z),
        (X - 1, Y + 1, Z),
        (X - 1, Y - 1, Z),
        (X, Y + 1, Z + 1),
        (X, Y - 1, Z + 1),
        (X, Y + 1, Z - 1),
        (X, Y - 1, Z - 1),
    ]
}

pub(in crate::Escape) fn EraseAccessPathLoops(
    Values: impl IntoIterator<Item = Position>,
) -> Vec<Position> {
    let mut Result = Vec::new();
    let mut PositionIndices = HashMap::new();
    for PositionValue in Values {
        if let Some(PriorIndex) = PositionIndices.get(&PositionValue).copied() {
            for Removed in Result.drain((PriorIndex + 1)..) {
                PositionIndices.remove(&Removed);
            }
            continue;
        }
        PositionIndices.insert(PositionValue, Result.len());
        Result.push(PositionValue);
    }
    Result
}

pub(in crate::Escape) fn BuildDeferredAccessCandidate(
    Variable: String,
    CandidateId: String,
    OwnerSignal: String,
    IngressY: i32,
    WirePath: Vec<Position>,
) -> Option<DeferredAccessCandidateValue> {
    let Portal = *WirePath.last()?;
    let OrderedWire = EraseAccessPathLoops(WirePath);
    let Wire = OrderedWire.iter().copied().collect::<BTreeSet<_>>();
    if Wire.is_empty() {
        return None;
    }
    let Support = Wire
        .iter()
        .map(|(X, Y, Z)| (*X, Y - 1, *Z))
        .collect::<BTreeSet<_>>();
    let mut Air = BTreeSet::new();
    for PositionValue in &Wire {
        for Neighbor in AccessNeighborPositions(*PositionValue) {
            if Neighbor <= *PositionValue
                || Neighbor.1 == PositionValue.1
                || !Wire.contains(&Neighbor)
            {
                continue;
            }
            let Lower = if PositionValue.1 < Neighbor.1 {
                *PositionValue
            } else {
                Neighbor
            };
            Air.insert((Lower.0, Lower.1 + 1, Lower.2));
        }
    }
    if !Air.is_disjoint(&Wire)
        || Wire.iter().any(|(X, Y, Z)| {
            let SupportPosition = (*X, Y - 1, *Z);
            Wire.contains(&SupportPosition) || Air.contains(&SupportPosition)
        })
    {
        return None;
    }
    let Electrical = Wire
        .iter()
        .flat_map(|PositionValue| {
            std::iter::once(*PositionValue).chain(AccessNeighborPositions(*PositionValue))
        })
        .collect::<BTreeSet<_>>();
    Some(DeferredAccessCandidateValue {
        Variable,
        CandidateId,
        OwnerSignal,
        IngressY,
        Portal,
        OrderedWire,
        Wire: Wire.into_iter().collect(),
        Support: Support.into_iter().collect(),
        Air: Air.into_iter().collect(),
        Electrical: Electrical.into_iter().collect(),
    })
}

pub(in crate::Escape) fn SortedAccessPositionsIntersect(
    First: &[Position],
    Second: &[Position],
) -> bool {
    let mut FirstIndex = 0usize;
    let mut SecondIndex = 0usize;
    while FirstIndex < First.len() && SecondIndex < Second.len() {
        match First[FirstIndex].cmp(&Second[SecondIndex]) {
            std::cmp::Ordering::Less => FirstIndex += 1,
            std::cmp::Ordering::Greater => SecondIndex += 1,
            std::cmp::Ordering::Equal => return true,
        }
    }
    false
}

pub(in crate::Escape) fn CrossCandidateRequiredAir(
    First: &DeferredAccessCandidateValue,
    Second: &DeferredAccessCandidateValue,
) -> Vec<Position> {
    let mut Air = BTreeSet::new();
    for FirstPosition in &First.Wire {
        for SecondPosition in &Second.Wire {
            let (Lower, Higher) = if FirstPosition <= SecondPosition {
                (*FirstPosition, *SecondPosition)
            } else {
                (*SecondPosition, *FirstPosition)
            };
            if Higher.1 == Lower.1 || !AccessNeighborPositions(Lower).contains(&Higher) {
                continue;
            }
            Air.insert((Lower.0, Lower.1 + 1, Lower.2));
        }
    }
    Air.into_iter().collect()
}

pub(in crate::Escape) fn DeferredAccessCandidatesConflict(
    First: &DeferredAccessCandidateValue,
    Second: &DeferredAccessCandidateValue,
) -> bool {
    let StaticConflict = SortedAccessPositionsIntersect(&First.Support, &Second.Wire)
        || SortedAccessPositionsIntersect(&First.Support, &Second.Air)
        || SortedAccessPositionsIntersect(&Second.Support, &First.Wire)
        || SortedAccessPositionsIntersect(&Second.Support, &First.Air)
        || SortedAccessPositionsIntersect(&First.Air, &Second.Wire)
        || SortedAccessPositionsIntersect(&Second.Air, &First.Wire);
    if StaticConflict {
        return true;
    }
    let CrossAir = CrossCandidateRequiredAir(First, Second);
    if SortedAccessPositionsIntersect(&CrossAir, &First.Wire)
        || SortedAccessPositionsIntersect(&CrossAir, &Second.Wire)
        || SortedAccessPositionsIntersect(&CrossAir, &First.Support)
        || SortedAccessPositionsIntersect(&CrossAir, &Second.Support)
    {
        return true;
    }
    First.OwnerSignal != Second.OwnerSignal
        && (SortedAccessPositionsIntersect(&First.Wire, &Second.Electrical)
            || SortedAccessPositionsIntersect(&Second.Wire, &First.Electrical))
}

pub(in crate::Escape) struct LayeredFrozenBaseClaimIndex {
    pub(in crate::Escape) Wire: HashSet<Position>,
    pub(in crate::Escape) Support: HashSet<Position>,
    pub(in crate::Escape) Air: HashSet<Position>,
    pub(in crate::Escape) WireOwners: HashMap<Position, HashSet<String>>,
    pub(in crate::Escape) ElectricalOwners: HashMap<Position, HashSet<String>>,
}

impl LayeredFrozenBaseClaimIndex {
    pub(in crate::Escape) fn New(Values: &[DeferredAccessCandidateValue]) -> Self {
        let mut Result = Self {
            Wire: HashSet::new(),
            Support: HashSet::new(),
            Air: HashSet::new(),
            WireOwners: HashMap::new(),
            ElectricalOwners: HashMap::new(),
        };
        for Value in Values {
            Result.Wire.extend(Value.Wire.iter().copied());
            Result.Support.extend(Value.Support.iter().copied());
            Result.Air.extend(Value.Air.iter().copied());
            for PositionValue in &Value.Wire {
                Result
                    .WireOwners
                    .entry(*PositionValue)
                    .or_default()
                    .insert(Value.OwnerSignal.clone());
            }
            for PositionValue in &Value.Electrical {
                Result
                    .ElectricalOwners
                    .entry(*PositionValue)
                    .or_default()
                    .insert(Value.OwnerSignal.clone());
            }
        }
        Result
    }

    pub(in crate::Escape) fn Conflicts(&self, Value: &DeferredAccessCandidateValue) -> bool {
        Value.Support.iter().any(|PositionValue| {
            self.Wire.contains(PositionValue) || self.Air.contains(PositionValue)
        }) || Value.Wire.iter().any(|PositionValue| {
            self.Support.contains(PositionValue)
                || self.Air.contains(PositionValue)
                || self
                    .ElectricalOwners
                    .get(PositionValue)
                    .is_some_and(|Owners| Owners.iter().any(|Owner| Owner != &Value.OwnerSignal))
        }) || Value.Air.iter().any(|PositionValue| {
            self.Support.contains(PositionValue) || self.Wire.contains(PositionValue)
        }) || Value.Electrical.iter().any(|PositionValue| {
            self.WireOwners
                .get(PositionValue)
                .is_some_and(|Owners| Owners.iter().any(|Owner| Owner != &Value.OwnerSignal))
        })
    }
}

pub(in crate::Escape) fn BuildFixedPrefixAccessCandidates(
    Requests: &[EscapeRequest],
    RequestMetadata: &[(String, String, String)],
) -> HashMap<String, DeferredAccessCandidateValue> {
    let MetadataByRequestId = RequestMetadata
        .iter()
        .map(|(RequestId, Variable, OwnerSignal)| (RequestId.as_str(), (Variable, OwnerSignal)))
        .collect::<HashMap<_, _>>();
    Requests
        .iter()
        .filter_map(|Request| {
            let (Variable, OwnerSignal) = MetadataByRequestId.get(Request.0.as_str())?;
            BuildDeferredAccessCandidate(
                (*Variable).clone(),
                format!("{}#fixed-prefix", Request.0),
                (*OwnerSignal).clone(),
                Request.1 .1,
                EraseAccessPathLoops(Request.4.iter().copied()),
            )
            .map(|Value| ((*Variable).clone(), Value))
        })
        .collect()
}

pub(in crate::Escape) fn ExactLayeredAccessPathCanCarryPower(
    SourceToPortal: bool,
    WirePath: &[Position],
) -> bool {
    if WirePath.len() <= 1 {
        return true;
    }
    let OrderedPath = if SourceToPortal {
        WirePath.to_vec()
    } else {
        WirePath.iter().rev().copied().collect::<Vec<_>>()
    };
    let mut Power = 15u8;
    for Index in 0..OrderedPath.len() - 1 {
        let mut NextPower = Power.saturating_sub(1);
        if Index > 0 && Index + 1 < OrderedPath.len() && Power > 0 {
            let Previous = OrderedPath[Index - 1];
            let Current = OrderedPath[Index];
            let Next = OrderedPath[Index + 1];
            let Incoming = (
                Current.0 - Previous.0,
                Current.1 - Previous.1,
                Current.2 - Previous.2,
            );
            let Outgoing = (Next.0 - Current.0, Next.1 - Current.1, Next.2 - Current.2);
            if Incoming == Outgoing && Incoming.1 == 0 && Incoming.0.abs() + Incoming.2.abs() == 1 {
                NextPower = 15;
            }
        }
        Power = NextPower;
        if Power == 0 {
            return false;
        }
    }
    true
}

pub(in crate::Escape) fn BuildDeferredLayeredAccessCandidates(
    Requests: &[EscapeRequest],
    RequestResults: &[EscapeRequestResult],
    RequestMetadata: &[(String, String, String)],
    Deadline: &RuntimeDeadline,
) -> PyResult<Option<(BTreeMap<String, String>, Vec<DeferredAccessCandidateValue>)>> {
    let RequestById = Requests
        .iter()
        .map(|Request| (Request.0.clone(), Request))
        .collect::<HashMap<_, _>>();
    let MetadataById = RequestMetadata
        .iter()
        .map(|(RequestId, Variable, OwnerSignal)| {
            (RequestId.clone(), (Variable.clone(), OwnerSignal.clone()))
        })
        .collect::<HashMap<_, _>>();
    if MetadataById.len() != RequestMetadata.len()
        || RequestById.len() != Requests.len()
        || RequestById
            .keys()
            .any(|RequestId| !MetadataById.contains_key(RequestId))
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "layered access request metadata must exactly own every request",
        ));
    }
    let mut RequiredVariables = BTreeMap::<String, String>::new();
    for (_RequestId, Variable, OwnerSignal) in RequestMetadata {
        if !Variable.starts_with("__access_terminal__:")
            || Variable == "__access_terminal__:"
            || OwnerSignal.is_empty()
        {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "layered access variables and owners must be nonempty: request={:?} variable={:?} owner={:?}",
                _RequestId, Variable, OwnerSignal,
            )));
        }
        if RequiredVariables
            .insert(Variable.clone(), OwnerSignal.clone())
            .is_some_and(|Existing| Existing != *OwnerSignal)
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "one layered access variable cannot have multiple owners",
            ));
        }
    }
    let mut DeferredValues = Vec::new();
    let mut SeenPhysicalValues = BTreeSet::new();
    for IncludePowerAlternatives in [false, true] {
        for (ResultIndex, (RequestId, Candidates, _ExpansionCount, Complete)) in
            RequestResults.iter().enumerate()
        {
            if ResultIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                return Ok(None);
            }
            if !Complete {
                return Ok(None);
            }
            let Some(Request) = RequestById.get(RequestId) else {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "layered access result references an unknown request",
                ));
            };
            let Some((Variable, OwnerSignal)) = MetadataById.get(RequestId) else {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "layered access result has no variable owner",
                ));
            };
            let mut SeenIngresses = HashMap::<Position, usize>::new();
            let mut OriginalCandidateCount = 0usize;
            for (CandidateIndex, (Ingress, _Direction, Path)) in Candidates.iter().enumerate() {
                if CandidateIndex % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
                    return Ok(None);
                }
                let IngressOccurrence = SeenIngresses.entry(*Ingress).or_insert(0);
                let IsPowerAlternative = Request.6 && *IngressOccurrence > 0;
                let OriginalCandidateIndex = if IsPowerAlternative {
                    OriginalCandidateCount.saturating_sub(1)
                } else {
                    let Value = OriginalCandidateCount;
                    OriginalCandidateCount += 1;
                    Value
                };
                *IngressOccurrence += 1;
                if IsPowerAlternative != IncludePowerAlternatives {
                    continue;
                }
                let WirePath = EraseAccessPathLoops(
                    Request
                        .4
                        .iter()
                        .copied()
                        .chain(Path.iter().copied().skip(1)),
                );
                if !SeenPhysicalValues.insert((Variable.clone(), WirePath.clone())) {
                    continue;
                }
                let CandidateId = if IsPowerAlternative {
                    if *IngressOccurrence == 2 {
                        format!("{}#{}:power", RequestId, OriginalCandidateIndex)
                    } else {
                        format!(
                            "{}#{}:power:{}",
                            RequestId,
                            OriginalCandidateIndex,
                            IngressOccurrence.saturating_sub(1),
                        )
                    }
                } else {
                    format!("{}#{}", RequestId, OriginalCandidateIndex)
                };
                if let Some(Value) = BuildDeferredAccessCandidate(
                    Variable.clone(),
                    CandidateId,
                    OwnerSignal.clone(),
                    Ingress.1,
                    WirePath,
                ) {
                    DeferredValues.push(Value);
                }
            }
        }
    }
    // Preserve physically distinct powered alternatives.  A longer wire set
    // may be the first path with legal repeater sites, so subset dominance is
    // not sound for the exact layered power contract.
    DeferredValues.sort_by(|First, Second| {
        First
            .Variable
            .cmp(&Second.Variable)
            .then_with(|| First.Portal.cmp(&Second.Portal))
            .then_with(|| First.Wire.len().cmp(&Second.Wire.len()))
            .then_with(|| First.Wire.cmp(&Second.Wire))
            .then_with(|| First.CandidateId.cmp(&Second.CandidateId))
    });
    Ok(Some((RequiredVariables, DeferredValues)))
}

pub(in crate::Escape) fn BuildLayeredAccessCandidateGroups(
    Requests: &[EscapeRequest],
    RequestResults: &[EscapeRequestResult],
    RequestMetadata: &[(String, String, String)],
    Deadline: &RuntimeDeadline,
) -> PyResult<Option<(BTreeMap<String, Vec<AssignmentCandidate>>, usize)>> {
    let Some((RequiredVariables, DeferredValues)) =
        BuildDeferredLayeredAccessCandidates(Requests, RequestResults, RequestMetadata, Deadline)?
    else {
        return Ok(None);
    };
    let ResourcePositions = DeferredValues
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
        .collect::<BTreeSet<_>>();
    let ResourceIndex = ResourcePositions
        .into_iter()
        .enumerate()
        .map(|(Index, PositionValue)| (PositionValue, Index))
        .collect::<HashMap<_, _>>();
    let ResourceCount = ResourceIndex.len().max(1);
    let mut Groups = RequiredVariables
        .keys()
        .map(|Variable| (Variable.clone(), Vec::new()))
        .collect::<BTreeMap<_, _>>();
    for (Index, Value) in DeferredValues.into_iter().enumerate() {
        if Index % DEADLINE_CHECK_INTERVAL == 0 && Deadline.Check() {
            return Ok(None);
        }
        let Remap = |Positions: &[Position]| {
            Positions
                .iter()
                .map(|PositionValue| ResourceIndex[PositionValue])
                .collect::<Vec<_>>()
        };
        let Claims = match ClaimMask::FromIndicesWithDeadline(
            ResourceCount,
            &Remap(&Value.Wire),
            &Remap(&Value.Support),
            &Remap(&Value.Air),
            &Remap(&Value.Electrical),
            Deadline,
        ) {
            Ok(Value) => Arc::new(Value),
            Err(ClaimMaskBuildError::DeadlineExceeded) => return Ok(None),
            Err(ClaimMaskBuildError::IndexOutOfRange) => {
                unreachable!("layered access positions were indexed before claim construction")
            }
        };
        let LogicalKey = Value
            .Variable
            .strip_prefix("__access_terminal__:")
            .expect("validated layered access variable");
        let Contract = format!(
            "access-stub:{}={};access-portal:{}={};access-layer:{}={}",
            LogicalKey,
            Value.CandidateId,
            LogicalKey,
            LayeredAccessPortalContractValue(Value.Portal),
            Value.OwnerSignal,
            Value.IngressY,
        );
        Groups
            .get_mut(&Value.Variable)
            .expect("validated layered access variable owns a group")
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
    Ok(Some((Groups, ResourceCount)))
}
