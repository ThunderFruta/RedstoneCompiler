use super::*;

#[derive(Clone)]
pub(in crate::Escape) struct DeferredGuideCandidateValue {
    pub(in crate::Escape) Variable: String,
    pub(in crate::Escape) CandidateId: String,
    pub(in crate::Escape) OwnerSignal: String,
    pub(in crate::Escape) Requirements: Vec<(String, String)>,
    pub(in crate::Escape) Portals: Vec<Position>,
    pub(in crate::Escape) RoutingY: i32,
    pub(in crate::Escape) Axis: String,
    pub(in crate::Escape) Lane: i32,
    pub(in crate::Escape) Guide: Vec<Position>,
    pub(in crate::Escape) AccessRamps: Vec<Vec<Position>>,
    pub(in crate::Escape) DetailedHintPaths: Vec<Vec<Position>>,
    pub(in crate::Escape) CertifiedRepeaters: Vec<(Position, String)>,
    pub(in crate::Escape) PhysicalGuide: Vec<Position>,
    pub(in crate::Escape) SupportedAccessChoices: BTreeSet<(String, String)>,
    pub(in crate::Escape) CertifiedAccessTuples: Arc<Vec<Vec<(String, String)>>>,
    pub(in crate::Escape) TerminalVariables: Vec<String>,
    pub(in crate::Escape) DetachedSeedAccessPaths: Vec<Vec<Position>>,
    pub(in crate::Escape) SourceTerminalVariable: Option<String>,
    pub(in crate::Escape) SourceDetachedAnchorIndex: Option<usize>,
    pub(in crate::Escape) PoweredCorridorHint: bool,
    pub(in crate::Escape) Claims: DeferredAccessCandidateValue,
    pub(in crate::Escape) Priority: (usize, usize, usize, usize, usize, usize, String, i32),
}

pub(in crate::Escape) fn LayeredAccessPortalContractValue((X, Y, Z): Position) -> String {
    format!("{X},{Y},{Z}")
}

pub(in crate::Escape) fn BuildLayeredGuideAccessContract(
    Requirements: &[(String, String)],
    AccessValueByChoice: &HashMap<(String, String), Position>,
) -> String {
    Requirements
        .iter()
        .map(|(Variable, CandidateId)| {
            let LogicalKey = Variable
                .strip_prefix("__access_terminal__:")
                .expect("validated guide access requirement");
            let AccessPortal = AccessValueByChoice
                .get(&(Variable.clone(), CandidateId.clone()))
                .expect("validated guide access requirement names an access value");
            format!(
                "access-portal:{}={}",
                LogicalKey,
                LayeredAccessPortalContractValue(*AccessPortal),
            )
        })
        .collect::<Vec<_>>()
        .join(";")
}

pub(in crate::Escape) fn LayeredAccessTerminalVariablePosition(Variable: &str) -> Option<Position> {
    let (_Identity, EncodedPosition) = Variable.rsplit_once('@')?;
    let mut Coordinates = EncodedPosition.split(',');
    let X = Coordinates.next()?.parse().ok()?;
    let Y = Coordinates.next()?.parse().ok()?;
    let Z = Coordinates.next()?.parse().ok()?;
    Coordinates.next().is_none().then_some((X, Y, Z))
}

pub(in crate::Escape) fn LayeredGuideTerminalSpan(TerminalVariables: &[String]) -> i32 {
    let Positions = TerminalVariables
        .iter()
        .filter_map(|Variable| LayeredAccessTerminalVariablePosition(Variable))
        .collect::<Vec<_>>();
    if Positions.len() != TerminalVariables.len() || Positions.is_empty() {
        return 0;
    }
    let MinimumX = Positions.iter().map(|Position| Position.0).min().unwrap();
    let MaximumX = Positions.iter().map(|Position| Position.0).max().unwrap();
    let MinimumZ = Positions.iter().map(|Position| Position.2).min().unwrap();
    let MaximumZ = Positions.iter().map(|Position| Position.2).max().unwrap();
    MaximumX
        .saturating_sub(MinimumX)
        .saturating_add(MaximumZ.saturating_sub(MinimumZ))
}

pub(in crate::Escape) fn CandidateLayeredGuideLanes(
    Center: i32,
    Count: usize,
    Pitch: i32,
) -> Vec<i32> {
    let mut Result = vec![Center];
    let mut Offset = Pitch;
    while Result.len() < Count {
        Result.push(Center - Offset);
        if Result.len() < Count {
            Result.push(Center + Offset);
        }
        Offset += Pitch;
    }
    Result
}

pub(in crate::Escape) fn RasterizeLayeredGuideSegment(
    First: (i32, i32),
    Second: (i32, i32),
    Result: &mut BTreeSet<(i32, i32)>,
) {
    if First.0 == Second.0 {
        for Z in First.1.min(Second.1)..=First.1.max(Second.1) {
            Result.insert((First.0, Z));
        }
    } else if First.1 == Second.1 {
        for X in First.0.min(Second.0)..=First.0.max(Second.0) {
            Result.insert((X, First.1));
        }
    }
}

pub(in crate::Escape) fn BuildLayeredGuideSpine(
    Terminals: &[(i32, i32)],
    Axis: &str,
    Lane: i32,
    RoutingY: i32,
) -> Vec<Position> {
    let mut Guide = BTreeSet::new();
    if Axis == "X" {
        let Minimum = Terminals.iter().map(|Value| Value.0).min().unwrap_or(0);
        let Maximum = Terminals.iter().map(|Value| Value.0).max().unwrap_or(0);
        RasterizeLayeredGuideSegment((Minimum, Lane), (Maximum, Lane), &mut Guide);
        for (X, Z) in Terminals {
            RasterizeLayeredGuideSegment((*X, *Z), (*X, Lane), &mut Guide);
        }
    } else {
        let Minimum = Terminals.iter().map(|Value| Value.1).min().unwrap_or(0);
        let Maximum = Terminals.iter().map(|Value| Value.1).max().unwrap_or(0);
        RasterizeLayeredGuideSegment((Lane, Minimum), (Lane, Maximum), &mut Guide);
        for (X, Z) in Terminals {
            RasterizeLayeredGuideSegment((*X, *Z), (Lane, *Z), &mut Guide);
        }
    }
    Guide.into_iter().map(|(X, Z)| (X, RoutingY, Z)).collect()
}

pub(in crate::Escape) fn LayeredAccessTupleIsSelfLegal(
    Values: &[&DeferredAccessCandidateValue],
) -> bool {
    Values.iter().enumerate().all(|(Index, First)| {
        Values
            .iter()
            .skip(Index + 1)
            .all(|Second| !DeferredAccessCandidatesConflict(First, Second))
    })
}

pub(in crate::Escape) fn FindCompleteLayeredAccessWitness(
    AccessByVariable: &BTreeMap<String, Vec<&DeferredAccessCandidateValue>>,
    Deadline: &RuntimeDeadline,
) -> Option<HashMap<String, String>> {
    // This witness only seeds portal diversity; it is never a feasibility
    // proof and the complete catalog remains available when it is absent.
    // Keep its bounded lookahead small so a hard access-only member cannot
    // consume the portfolio's absolute routing deadline.
    const MAXIMUM_ACCESS_CERTIFICATE_EXPANSIONS: usize = 512;
    let mut Variables = AccessByVariable.keys().cloned().collect::<Vec<_>>();
    Variables.sort_by_key(|Variable| (AccessByVariable[Variable].len(), Variable.clone()));
    let mut Selected = HashMap::<String, &DeferredAccessCandidateValue>::new();
    let mut ExpansionCount = 0usize;

    fn Search<'a>(
        VariableIndex: usize,
        Variables: &[String],
        AccessByVariable: &BTreeMap<String, Vec<&'a DeferredAccessCandidateValue>>,
        Selected: &mut HashMap<String, &'a DeferredAccessCandidateValue>,
        ExpansionCount: &mut usize,
        Deadline: &RuntimeDeadline,
    ) -> bool {
        if Deadline.Check() || *ExpansionCount >= MAXIMUM_ACCESS_CERTIFICATE_EXPANSIONS {
            return false;
        }
        if VariableIndex == Variables.len() {
            return true;
        }
        let Variable = &Variables[VariableIndex];
        for Candidate in &AccessByVariable[Variable] {
            if Deadline.Check() || *ExpansionCount >= MAXIMUM_ACCESS_CERTIFICATE_EXPANSIONS {
                return false;
            }
            if Selected
                .values()
                .any(|Previous| DeferredAccessCandidatesConflict(Candidate, Previous))
            {
                continue;
            }
            *ExpansionCount += 1;
            Selected.insert(Variable.clone(), Candidate);
            if Search(
                VariableIndex + 1,
                Variables,
                AccessByVariable,
                Selected,
                ExpansionCount,
                Deadline,
            ) {
                return true;
            }
            Selected.remove(Variable);
        }
        false
    }

    let Complete = Search(
        0,
        &Variables,
        AccessByVariable,
        &mut Selected,
        &mut ExpansionCount,
        Deadline,
    );
    if std::env::var_os("RCS_DEBUG_NATIVE_ACCESS_GUIDE").is_some() {
        eprintln!(
            "native layered complete access certificate complete={} variables={} expansions={}",
            Complete,
            Variables.len(),
            ExpansionCount,
        );
    }
    Complete.then(|| {
        Selected
            .into_iter()
            .map(|(Variable, Candidate)| (Variable, Candidate.CandidateId.clone()))
            .collect()
    })
}
