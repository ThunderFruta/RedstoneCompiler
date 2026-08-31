//! Frozen exterior-connector and fabric-subtree geometry kernels.

use crate::Core::Models::{Edge, Position};
use crate::Core::Runtime::RoutingThreadPool;
use crate::Geometry::RouteClaims::NormalizeEdge;
use pyo3::prelude::*;
use std::cmp::Reverse;
use std::collections::{BTreeSet, BinaryHeap, HashMap, HashSet, VecDeque};
use std::sync::Arc;

type FabricSubtreeValues = Option<(Vec<Position>, Vec<Edge>)>;
type ExteriorConnectorFieldValues = (
    Vec<Position>,
    Position,
    Position,
    Vec<(i32, i32)>,
    (i32, i32, i32, i32),
    Vec<Position>,
    Vec<Edge>,
);
type ExteriorConnectorRequestValues = (usize, Position, Vec<Position>);
type ExteriorConnectorResultValues = (Vec<Position>, bool, bool, usize);

struct FrozenExteriorConnectorField {
    Targets: Vec<Position>,
    TargetSet: HashSet<Position>,
    BlockedGuideCells: HashSet<(i32, i32)>,
    Bounds: (i32, i32, i32, i32),
    AllowedNodes: HashSet<Position>,
    AllowedEdges: HashSet<Edge>,
}

fn SearchExteriorConnector(
    Field: &FrozenExteriorConnectorField,
    Values: ExteriorConnectorRequestValues,
) -> ExteriorConnectorResultValues {
    let (_FieldIndex, Start, BlockedLocalNodes) = Values;
    let mut Targets = Field.Targets.clone();
    Targets.sort_unstable_by_key(|Target| {
        (
            (Target.0 - Start.0).abs() + (Target.2 - Start.2).abs(),
            *Target,
        )
    });
    Targets.dedup();
    let BlockedLocalSet = BlockedLocalNodes.into_iter().collect::<HashSet<_>>();
    let NodeIsLegal = |PositionValue: Position| {
        let (X, _Y, Z) = PositionValue;
        Field.Bounds.0 <= X
            && X <= Field.Bounds.1
            && Field.Bounds.2 <= Z
            && Z <= Field.Bounds.3
            && !BlockedLocalSet.contains(&PositionValue)
            && Field.AllowedNodes.contains(&PositionValue)
            && !Field.BlockedGuideCells.contains(&(X, Z))
    };
    let EdgeIsLegal = |First: Position, Second: Position| {
        Field.AllowedEdges.contains(&NormalizeEdge(First, Second))
    };
    let BuildAxisOrderedCandidate = |Target: Position, FirstAxis: usize| {
        let mut Current = Start;
        let mut Result = vec![Start];
        for Axis in [FirstAxis, if FirstAxis == 0 { 2 } else { 0 }] {
            let TargetValue = match Axis {
                0 => Target.0,
                2 => Target.2,
                _ => unreachable!(),
            };
            loop {
                let CurrentValue = match Axis {
                    0 => Current.0,
                    2 => Current.2,
                    _ => unreachable!(),
                };
                if CurrentValue == TargetValue {
                    break;
                }
                let Delta = if TargetValue > CurrentValue { 1 } else { -1 };
                Current = match Axis {
                    0 => (Current.0 + Delta, Current.1, Current.2),
                    2 => (Current.0, Current.1, Current.2 + Delta),
                    _ => unreachable!(),
                };
                Result.push(Current);
            }
        }
        Result
    };
    for Target in &Targets {
        for FirstAxis in [0usize, 2usize] {
            let Candidate = BuildAxisOrderedCandidate(*Target, FirstAxis);
            if Candidate.iter().skip(1).copied().all(NodeIsLegal)
                && Candidate
                    .windows(2)
                    .all(|Values| EdgeIsLegal(Values[0], Values[1]))
            {
                return (Candidate, true, false, 0);
            }
        }
    }
    let UseManhattanTargetHeuristic = Targets.len() <= 16;
    let TargetDistance = |PositionValue: Position| {
        if !UseManhattanTargetHeuristic {
            return 0;
        }
        Targets
            .iter()
            .map(|Target| (Target.0 - PositionValue.0).abs() + (Target.2 - PositionValue.2).abs())
            .min()
            .unwrap_or(0)
    };
    let mut Pending = BinaryHeap::new();
    Pending.push(Reverse((TargetDistance(Start), 0i32, Start)));
    let mut Previous = HashMap::<Position, Option<Position>>::new();
    Previous.insert(Start, None);
    let mut PathDistance = HashMap::<Position, i32>::new();
    PathDistance.insert(Start, 0);
    let mut ExpansionCount = 0usize;
    while let Some(Reverse((_Priority, CurrentDistance, Current))) = Pending.pop() {
        if PathDistance.get(&Current).copied() != Some(CurrentDistance) {
            continue;
        }
        ExpansionCount += 1;
        for Neighbor in [
            (Current.0 - 1, Current.1, Current.2),
            (Current.0 + 1, Current.1, Current.2),
            (Current.0, Current.1, Current.2 - 1),
            (Current.0, Current.1, Current.2 + 1),
        ] {
            if !NodeIsLegal(Neighbor) || !EdgeIsLegal(Current, Neighbor) {
                continue;
            }
            let NeighborDistance = CurrentDistance + 1;
            if NeighborDistance >= PathDistance.get(&Neighbor).copied().unwrap_or(i32::MAX) {
                continue;
            }
            Previous.insert(Neighbor, Some(Current));
            PathDistance.insert(Neighbor, NeighborDistance);
            if Field.TargetSet.contains(&Neighbor) {
                let mut Path = vec![Neighbor];
                while let Some(Some(Parent)) = Previous.get(Path.last().expect("path has target")) {
                    Path.push(*Parent);
                }
                Path.reverse();
                return (Path, false, true, ExpansionCount);
            }
            Pending.push(Reverse((
                NeighborDistance + TargetDistance(Neighbor),
                NeighborDistance,
                Neighbor,
            )));
        }
    }
    (Vec::new(), false, true, ExpansionCount)
}

/// Execute independent frozen exterior searches in fixed worker shards.
/// Results retain request order; Python performs all mutable cache updates and
/// final redstone claim validation after this function returns.
fn SearchExteriorConnectorsBatchNative(
    FieldValues: Vec<ExteriorConnectorFieldValues>,
    Requests: Vec<ExteriorConnectorRequestValues>,
) -> (Vec<ExteriorConnectorResultValues>, usize) {
    let Fields = FieldValues
        .into_iter()
        .map(
            |(
                Targets,
                _EnvelopeMinimum,
                _EnvelopeMaximum,
                BlockedGuideCells,
                Bounds,
                AllowedNodes,
                AllowedEdges,
            )| FrozenExteriorConnectorField {
                TargetSet: Targets.iter().copied().collect(),
                Targets,
                BlockedGuideCells: BlockedGuideCells.into_iter().collect(),
                Bounds,
                AllowedNodes: AllowedNodes.into_iter().collect(),
                AllowedEdges: AllowedEdges.into_iter().collect(),
            },
        )
        .collect::<Vec<_>>();
    if Requests
        .iter()
        .any(|(FieldIndex, _, _)| *FieldIndex >= Fields.len())
    {
        panic!("exterior connector request references a missing frozen field");
    }
    let Pool = RoutingThreadPool();
    let WorkerCount = Pool.current_num_threads().min(Requests.len());
    if WorkerCount < 2 {
        return (
            Requests
                .into_iter()
                .map(|Request| SearchExteriorConnector(&Fields[Request.0], Request))
                .collect(),
            WorkerCount,
        );
    }
    let SharedFields = Arc::new(Fields);
    let SharedRequests = Arc::new(Requests);
    let Shards = Pool.broadcast(|Context| {
        let WorkerIndex = Context.index();
        let WorkerTotal = Context.num_threads();
        let Values = (WorkerIndex..SharedRequests.len())
            .step_by(WorkerTotal)
            .map(|Index| {
                let Request = SharedRequests[Index].clone();
                (
                    Index,
                    SearchExteriorConnector(&SharedFields[Request.0], Request),
                )
            })
            .collect::<Vec<_>>();
        (WorkerIndex, Values)
    });
    let mut Ordered = (0..SharedRequests.len())
        .map(|_| None)
        .collect::<Vec<Option<ExteriorConnectorResultValues>>>();
    let mut ActiveWorkers = 0usize;
    for (_WorkerIndex, Values) in Shards {
        if !Values.is_empty() {
            ActiveWorkers += 1;
        }
        for (Index, Value) in Values {
            Ordered[Index] = Some(Value);
        }
    }
    (
        Ordered
            .into_iter()
            .map(|Value| Value.expect("every exterior connector batch item needs one worker"))
            .collect(),
        ActiveWorkers,
    )
}

#[pyfunction]
pub(crate) fn SearchExteriorConnectorsBatchWithTelemetry(
    PythonValue: Python<'_>,
    FieldValues: Vec<ExteriorConnectorFieldValues>,
    Requests: Vec<ExteriorConnectorRequestValues>,
) -> (Vec<ExteriorConnectorResultValues>, usize) {
    PythonValue.allow_threads(|| SearchExteriorConnectorsBatchNative(FieldValues, Requests))
}

fn BuildFabricSubtreeValues(
    Adjacency: &HashMap<Position, Vec<Position>>,
    Attachments: Vec<Position>,
) -> FabricSubtreeValues {
    let Required = Attachments.into_iter().collect::<BTreeSet<_>>();
    let Root = *Required.iter().next()?;
    let mut Parents = HashMap::new();
    Parents.insert(Root, None::<Position>);
    let mut Pending = VecDeque::from([Root]);
    while let Some(Current) = Pending.pop_front() {
        for Neighbor in Adjacency.get(&Current).into_iter().flatten() {
            if Parents.contains_key(Neighbor) {
                continue;
            }
            Parents.insert(*Neighbor, Some(Current));
            Pending.push_back(*Neighbor);
        }
    }
    if Required
        .iter()
        .any(|PositionValue| !Parents.contains_key(PositionValue))
    {
        return None;
    }
    let mut Nodes = BTreeSet::from([Root]);
    let mut Edges = BTreeSet::new();
    for Target in Required.iter().skip(1) {
        let mut Current = *Target;
        while Current != Root {
            let Parent = Parents[&Current].expect("reachable fabric node needs parent");
            Nodes.insert(Current);
            Nodes.insert(Parent);
            Edges.insert(NormalizeEdge(Current, Parent));
            Current = Parent;
        }
    }
    Some((Nodes.into_iter().collect(), Edges.into_iter().collect()))
}

fn BuildFabricSubtreesBatchNative(
    NodeValues: Vec<Position>,
    EdgeValues: Vec<Edge>,
    AttachmentSets: Vec<Vec<Position>>,
) -> (Vec<FabricSubtreeValues>, usize) {
    let mut Adjacency = NodeValues
        .into_iter()
        .map(|PositionValue| (PositionValue, Vec::new()))
        .collect::<HashMap<_, _>>();
    for (First, Second) in EdgeValues {
        if let Some(Values) = Adjacency.get_mut(&First) {
            Values.push(Second);
        }
        if let Some(Values) = Adjacency.get_mut(&Second) {
            Values.push(First);
        }
    }
    for Values in Adjacency.values_mut() {
        Values.sort_unstable();
        Values.dedup();
    }
    let Pool = RoutingThreadPool();
    let WorkerCount = Pool.current_num_threads().min(AttachmentSets.len());
    if WorkerCount < 2 {
        return (
            AttachmentSets
                .into_iter()
                .map(|Attachments| BuildFabricSubtreeValues(&Adjacency, Attachments))
                .collect(),
            WorkerCount,
        );
    }
    let SharedAdjacency = Arc::new(Adjacency);
    let SharedAttachments = Arc::new(AttachmentSets);
    let Shards = Pool.broadcast(|Context| {
        let WorkerIndex = Context.index();
        let WorkerTotal = Context.num_threads();
        let Values = (WorkerIndex..SharedAttachments.len())
            .step_by(WorkerTotal)
            .map(|Index| {
                (
                    Index,
                    BuildFabricSubtreeValues(&SharedAdjacency, SharedAttachments[Index].clone()),
                )
            })
            .collect::<Vec<_>>();
        (WorkerIndex, Values)
    });
    let mut Ordered = (0..SharedAttachments.len())
        .map(|_| None)
        .collect::<Vec<Option<FabricSubtreeValues>>>();
    let mut ActiveWorkers = 0usize;
    for (_WorkerIndex, Values) in Shards {
        if !Values.is_empty() {
            ActiveWorkers += 1;
        }
        for (Index, Value) in Values {
            Ordered[Index] = Some(Value);
        }
    }
    (
        Ordered
            .into_iter()
            .map(|Value| Value.expect("every fabric subtree batch item needs one worker"))
            .collect(),
        ActiveWorkers,
    )
}

/// Expand independent route-claim sets with the shared bounded Rayon pool.
/// The Python component frontier supplies only immutable node sets here; all
/// state merging and ownership decisions remain deterministic in Python.
#[pyfunction]
pub(crate) fn BuildFabricSubtreesBatchWithTelemetry(
    PythonValue: Python<'_>,
    NodeValues: Vec<Position>,
    EdgeValues: Vec<Edge>,
    AttachmentSets: Vec<Vec<Position>>,
) -> (Vec<FabricSubtreeValues>, usize) {
    PythonValue
        .allow_threads(|| BuildFabricSubtreesBatchNative(NodeValues, EdgeValues, AttachmentSets))
}
