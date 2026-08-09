#![allow(non_snake_case)]

mod Assignment;
mod AssignmentPlanning;
mod Bindings;
mod Deadline;
mod EscapePlanning;
mod Generation;
mod LeasePlanning;
mod Models;
mod PathRouting;

use Models::*;

use pyo3::prelude::*;
use rayon::prelude::*;
use rayon::{ThreadPool, ThreadPoolBuilder};
use std::cmp::Reverse;
use std::collections::{BTreeSet, BinaryHeap, HashMap, HashSet, VecDeque};
use std::sync::{Arc, OnceLock};

pub(crate) fn RoutingThreadPool() -> &'static ThreadPool {
    static POOL: OnceLock<ThreadPool> = OnceLock::new();
    POOL.get_or_init(|| {
        let Available = std::thread::available_parallelism()
            .map(|Value| Value.get())
            .unwrap_or(1);
        let Requested = std::env::var("RC_ROUTING_THREADS")
            .ok()
            .and_then(|Value| Value.parse::<usize>().ok())
            .filter(|Value| *Value > 0)
            // Detailed negotiated routing shares this pool with portal and
            // legacy batch work.  A moderate default leaves CPU headroom for
            // the Python coordinator and avoids oversubscribing the host;
            // callers that need a different cap can still set
            // RC_ROUTING_THREADS explicitly.
            .unwrap_or(Available.min(8));
        ThreadPoolBuilder::new()
            .num_threads(Requested.clamp(1, Available))
            .thread_name(|Index| format!("redstone-router-{Index}"))
            .build()
            .expect("could not create native routing thread pool")
    })
}

#[pyfunction]
fn GenerateRectilinearTopology(TerminalValues: Vec<Position2>) -> Vec<RectilinearEdge> {
    let mut Terminals = TerminalValues;
    Terminals.sort_unstable();
    Terminals.dedup();
    if Terminals.len() < 2 {
        return Vec::new();
    }
    let mut Tree = vec![Terminals.remove(0)];
    let mut Result = Vec::new();
    while !Terminals.is_empty() {
        let (TerminalIndex, Anchor) = Terminals
            .iter()
            .enumerate()
            .flat_map(|(Index, Terminal)| {
                Tree.iter().map(move |Existing| {
                    (
                        Index,
                        *Existing,
                        (Terminal.0 - Existing.0).abs() + (Terminal.1 - Existing.1).abs(),
                        *Terminal,
                    )
                })
            })
            .min_by_key(|(Index, Existing, Distance, Terminal)| {
                (*Distance, *Terminal, *Existing, *Index)
            })
            .map(|(Index, Existing, _Distance, _Terminal)| (Index, Existing))
            .unwrap();
        let Terminal = Terminals.remove(TerminalIndex);
        let Corner = (Terminal.0, Anchor.1);
        if Anchor != Corner {
            Result.push((Anchor, Corner));
        }
        if Corner != Terminal {
            Result.push((Corner, Terminal));
        }
        Tree.push(Terminal);
        if Corner != Anchor && Corner != Terminal {
            Tree.push(Corner);
        }
        Tree.sort_unstable();
        Tree.dedup();
    }
    Result
}

#[pyfunction]
fn GetRoutingThreadCount() -> usize {
    RoutingThreadPool().current_num_threads()
}

type RouteClaimValues = (Vec<Position>, Vec<Position>, Vec<Position>, Vec<Position>);
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
    EnvelopeMinimum: Position,
    EnvelopeMaximum: Position,
    BlockedGuideCells: HashSet<(i32, i32)>,
    Bounds: (i32, i32, i32, i32),
    AllowedNodes: HashSet<Position>,
    AllowedEdges: HashSet<Edge>,
}

fn NeighborPositions(PositionValue: Position) -> [Position; 12] {
    let (X, Y, Z) = PositionValue;
    [
        (X + 1, Y, Z),
        (X - 1, Y, Z),
        (X, Y, Z + 1),
        (X, Y, Z - 1),
        (X + 1, Y + 1, Z),
        (X - 1, Y + 1, Z),
        (X, Y + 1, Z + 1),
        (X, Y + 1, Z - 1),
        (X + 1, Y - 1, Z),
        (X - 1, Y - 1, Z),
        (X, Y - 1, Z + 1),
        (X, Y - 1, Z - 1),
    ]
}

fn BuildRouteClaimValues(
    Values: Vec<Position>,
    ActualBlocks: &std::collections::HashSet<Position>,
    SolidBlocks: &std::collections::HashSet<Position>,
) -> RouteClaimValues {
    let Wire: std::collections::BTreeSet<Position> = Values.into_iter().collect();
    let mut Support = std::collections::BTreeSet::new();
    let mut Air = std::collections::BTreeSet::new();
    let mut Electrical = Wire.clone();
    for PositionValue in &Wire {
        let (X, Y, Z) = *PositionValue;
        Support.insert((X, Y - 1, Z));
        for Neighbor in NeighborPositions(*PositionValue) {
            Electrical.insert(Neighbor);
            if Neighbor <= *PositionValue || !Wire.contains(&Neighbor) || Neighbor.1 == Y {
                continue;
            }
            let Lower = if Y < Neighbor.1 {
                *PositionValue
            } else {
                Neighbor
            };
            let Upper = if Y < Neighbor.1 {
                Neighbor
            } else {
                *PositionValue
            };
            let Headroom = (Lower.0, Lower.1 + 1, Lower.2);
            // This is RoutingResourceGraph.BuildPrimitive's vertical claim
            // rule.  An illegal primitive contributes no headroom claim.
            if (SolidBlocks.contains(&Headroom) || ActualBlocks.contains(&Headroom))
                || (ActualBlocks.contains(&(Upper.0, Upper.1 - 1, Upper.2))
                    && !SolidBlocks.contains(&(Upper.0, Upper.1 - 1, Upper.2)))
            {
                continue;
            }
            Air.insert(Headroom);
        }
    }
    (
        Wire.into_iter().collect(),
        Support.into_iter().collect(),
        Air.into_iter().collect(),
        Electrical.into_iter().collect(),
    )
}

fn NormalizeEdge(First: Position, Second: Position) -> Edge {
    if First <= Second {
        (First, Second)
    } else {
        (Second, First)
    }
}

/// Search one frozen exterior connector contract.  Claim construction and
/// final candidate validation deliberately stay in Python: this kernel sees
/// only immutable node/edge ownership that has already been frozen by the
/// component planner.
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
            && !(Field.EnvelopeMinimum.0 <= X
                && X <= Field.EnvelopeMaximum.0
                && Field.EnvelopeMinimum.2 <= Z
                && Z <= Field.EnvelopeMaximum.2)
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
    let TargetDistance = |PositionValue: Position| {
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
                EnvelopeMinimum,
                EnvelopeMaximum,
                BlockedGuideCells,
                Bounds,
                AllowedNodes,
                AllowedEdges,
            )| FrozenExteriorConnectorField {
                TargetSet: Targets.iter().copied().collect(),
                Targets,
                EnvelopeMinimum,
                EnvelopeMaximum,
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
fn SearchExteriorConnectorsBatchWithTelemetry(
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
fn BuildRouteClaimsBatchNative(
    NodeSets: Vec<Vec<Position>>,
    ActualBlockValues: Vec<Position>,
    SolidBlockValues: Vec<Position>,
) -> (Vec<RouteClaimValues>, usize) {
    let ActualBlocks: std::collections::HashSet<Position> = ActualBlockValues.into_iter().collect();
    let SolidBlocks: std::collections::HashSet<Position> = SolidBlockValues.into_iter().collect();
    let Pool = RoutingThreadPool();
    let WorkerCount = Pool.current_num_threads().min(NodeSets.len());
    if WorkerCount < 2 {
        return (
            NodeSets
                .into_iter()
                .map(|Values| BuildRouteClaimValues(Values, &ActualBlocks, &SolidBlocks))
                .collect(),
            WorkerCount,
        );
    }
    let SharedNodes = Arc::new(NodeSets);
    let SharedActualBlocks = Arc::new(ActualBlocks);
    let SharedSolidBlocks = Arc::new(SolidBlocks);
    // `broadcast` invokes the closure once on every native pool worker.  Each
    // worker receives a stable strided shard, which prevents a short batch
    // from being swallowed by one Rayon worker before the other seven begin.
    let Shards = Pool.broadcast(|Context| {
        let WorkerIndex = Context.index();
        let WorkerTotal = Context.num_threads();
        let Values = (WorkerIndex..SharedNodes.len())
            .step_by(WorkerTotal)
            .map(|Index| {
                (
                    Index,
                    BuildRouteClaimValues(
                        SharedNodes[Index].clone(),
                        &SharedActualBlocks,
                        &SharedSolidBlocks,
                    ),
                )
            })
            .collect::<Vec<_>>();
        (WorkerIndex, Values)
    });
    let mut Ordered = (0..SharedNodes.len())
        .map(|_| None)
        .collect::<Vec<Option<RouteClaimValues>>>();
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
            .map(|Value| Value.expect("every claim batch item needs one worker"))
            .collect(),
        ActiveWorkers,
    )
}

#[pyfunction]
fn BuildRouteClaimsBatch(
    PythonValue: Python<'_>,
    NodeSets: Vec<Vec<Position>>,
    ActualBlockValues: Vec<Position>,
    SolidBlockValues: Vec<Position>,
) -> Vec<RouteClaimValues> {
    PythonValue.allow_threads(|| {
        BuildRouteClaimsBatchNative(NodeSets, ActualBlockValues, SolidBlockValues).0
    })
}

#[pyfunction]
fn BuildRouteClaimsBatchWithTelemetry(
    PythonValue: Python<'_>,
    NodeSets: Vec<Vec<Position>>,
    ActualBlockValues: Vec<Position>,
    SolidBlockValues: Vec<Position>,
) -> (Vec<RouteClaimValues>, usize) {
    PythonValue.allow_threads(|| {
        BuildRouteClaimsBatchNative(NodeSets, ActualBlockValues, SolidBlockValues)
    })
}

#[pyfunction]
fn BuildFabricSubtreesBatchWithTelemetry(
    PythonValue: Python<'_>,
    NodeValues: Vec<Position>,
    EdgeValues: Vec<Edge>,
    AttachmentSets: Vec<Vec<Position>>,
) -> (Vec<FabricSubtreeValues>, usize) {
    PythonValue
        .allow_threads(|| BuildFabricSubtreesBatchNative(NodeValues, EdgeValues, AttachmentSets))
}

#[derive(Clone)]
struct LogicInstruction {
    Kind: u8,
    Inputs: Vec<(usize, bool)>,
    Outputs: Vec<usize>,
}

fn EvaluateLogicProgram(
    Assignment: usize,
    InputCount: usize,
    SignalCount: usize,
    Instructions: &[LogicInstruction],
    OutputIndices: &[usize],
    OutputEnabled: &[bool],
    Values: &mut Vec<bool>,
) -> u64 {
    Values.clear();
    Values.resize(SignalCount, false);
    for InputIndex in 0..InputCount {
        Values[InputIndex] = Assignment & (1usize << (InputCount - InputIndex - 1)) != 0;
    }
    for Instruction in Instructions {
        let InputValue = |(SignalIndex, Enabled): &(usize, bool)| *Enabled && Values[*SignalIndex];
        let Result = match Instruction.Kind {
            0 => !Instruction.Inputs.iter().all(InputValue),
            1 => Instruction.Inputs.iter().all(InputValue),
            2 => Instruction.Inputs.iter().any(InputValue),
            3 => {
                Instruction
                    .Inputs
                    .iter()
                    .filter(|Value| InputValue(Value))
                    .count()
                    % 2
                    == 1
            }
            4 => !InputValue(&Instruction.Inputs[0]),
            5 => InputValue(&Instruction.Inputs[0]),
            _ => unreachable!("logic instruction kind was validated"),
        };
        for Output in &Instruction.Outputs {
            Values[*Output] = Result;
        }
    }
    OutputIndices.iter().zip(OutputEnabled).enumerate().fold(
        0u64,
        |Mask, (OutputIndex, (SignalIndex, Enabled))| {
            if *Enabled && Values[*SignalIndex] {
                Mask | (1u64 << OutputIndex)
            } else {
                Mask
            }
        },
    )
}

#[allow(clippy::type_complexity)]
#[pyfunction]
fn EvaluateLogicPrograms(
    PythonValue: Python<'_>,
    InputCount: usize,
    ReferenceSignalCount: usize,
    ReferenceInstructionValues: Vec<(u8, Vec<(usize, bool)>, Vec<usize>)>,
    ReferenceOutputIndices: Vec<usize>,
    PhysicalSignalCount: usize,
    PhysicalInstructionValues: Vec<(u8, Vec<(usize, bool)>, Vec<usize>)>,
    PhysicalOutputIndices: Vec<usize>,
    PhysicalOutputEnabled: Vec<bool>,
) -> PyResult<Vec<(u64, u64)>> {
    if InputCount >= usize::BITS as usize {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "native exhaustive simulation has too many inputs",
        ));
    }
    if ReferenceOutputIndices.len() > 64
        || PhysicalOutputIndices.len() > 64
        || ReferenceOutputIndices.len() != PhysicalOutputIndices.len()
        || PhysicalOutputIndices.len() != PhysicalOutputEnabled.len()
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "native simulation requires matching output vectors of at most 64 signals",
        ));
    }
    let BuildInstructions = |Values: Vec<(u8, Vec<(usize, bool)>, Vec<usize>)>,
                             SignalCount: usize|
     -> PyResult<Vec<LogicInstruction>> {
        let mut Result = Vec::with_capacity(Values.len());
        for (Kind, Inputs, Outputs) in Values {
            if Kind > 5
                || Inputs.iter().any(|(Index, _Enabled)| *Index >= SignalCount)
                || Outputs.iter().any(|Index| *Index >= SignalCount)
                || ((Kind == 4 || Kind == 5) && Inputs.len() != 1)
            {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "invalid native logic instruction",
                ));
            }
            Result.push(LogicInstruction {
                Kind,
                Inputs,
                Outputs,
            });
        }
        Ok(Result)
    };
    let ReferenceInstructions =
        BuildInstructions(ReferenceInstructionValues, ReferenceSignalCount)?;
    let PhysicalInstructions = BuildInstructions(PhysicalInstructionValues, PhysicalSignalCount)?;
    if ReferenceOutputIndices
        .iter()
        .any(|Index| *Index >= ReferenceSignalCount)
        || PhysicalOutputIndices
            .iter()
            .any(|Index| *Index >= PhysicalSignalCount)
    {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "native simulation output references a missing signal",
        ));
    }
    let AssignmentCount = 1usize << InputCount;
    let ReferenceOutputEnabled = vec![true; ReferenceOutputIndices.len()];
    Ok(PythonValue.allow_threads(|| {
        RoutingThreadPool().install(|| {
            (0..AssignmentCount)
                .into_par_iter()
                .map_init(
                    || (Vec::new(), Vec::new()),
                    |(ReferenceValues, PhysicalValues), Assignment| {
                        let Expected = EvaluateLogicProgram(
                            Assignment,
                            InputCount,
                            ReferenceSignalCount,
                            &ReferenceInstructions,
                            &ReferenceOutputIndices,
                            &ReferenceOutputEnabled,
                            ReferenceValues,
                        );
                        let Simulated = EvaluateLogicProgram(
                            Assignment,
                            InputCount,
                            PhysicalSignalCount,
                            &PhysicalInstructions,
                            &PhysicalOutputIndices,
                            &PhysicalOutputEnabled,
                            PhysicalValues,
                        );
                        (Expected, Simulated)
                    },
                )
                .collect()
        })
    }))
}

#[pymodule]
fn RustRouting(Module: &Bound<'_, PyModule>) -> PyResult<()> {
    Bindings::Register(Module)
}

#[cfg(test)]
mod Tests {
    use super::*;
    use crate::Assignment::AssignCandidates;
    use crate::Deadline::RuntimeDeadline;
    use crate::PathRouting::FindPath;
    use std::collections::BTreeMap;
    use std::collections::{HashMap, HashSet};

    #[test]
    fn IndexedNandEvaluationPreservesAssignmentOrder() {
        let Instructions = vec![LogicInstruction {
            Kind: 0,
            Inputs: vec![(0, true), (1, true)],
            Outputs: vec![2],
        }];
        let mut Values = Vec::new();
        let Results: Vec<_> = (0..4)
            .map(|Assignment| {
                EvaluateLogicProgram(Assignment, 2, 3, &Instructions, &[2], &[true], &mut Values)
            })
            .collect();
        assert_eq!(Results, vec![1, 1, 1, 0]);
    }

    #[test]
    fn GraphTraversalCannotUseUnlistedTransition() {
        let A = (0, 0, 0);
        let B = (1, 0, 0);
        let C = (2, 0, 0);
        let Adjacency = HashMap::from([(A, vec![B]), (B, vec![A]), (C, vec![])]);
        let Result = FindPath(
            &Adjacency,
            &[A],
            C,
            0,
            &HashSet::new(),
            &HashMap::new(),
            &HashMap::new(),
            0,
            0,
            0,
            100,
        );
        assert!(Result.is_none());
    }

    #[test]
    fn ClaimMasksDetectRedstoneCrossCategoryConflicts() {
        let Wire = ClaimMask::FromIndices(8, &[2], &[], &[], &[2, 3]).unwrap();
        let Neighbor = ClaimMask::FromIndices(8, &[3], &[], &[], &[2, 3]).unwrap();
        let Isolated = ClaimMask::FromIndices(8, &[6], &[], &[], &[5, 6, 7]).unwrap();
        let SupportUnderWire = ClaimMask::FromIndices(8, &[], &[2], &[], &[]).unwrap();
        let SupportInAir = ClaimMask::FromIndices(8, &[], &[4], &[], &[]).unwrap();
        let RequiredAir = ClaimMask::FromIndices(8, &[], &[], &[4], &[]).unwrap();
        assert!(Wire.Conflicts(&Neighbor));
        assert!(Wire.Conflicts(&SupportUnderWire));
        assert!(SupportInAir.Conflicts(&RequiredAir));
        assert!(!Wire.Conflicts(&Isolated));
    }

    #[test]
    fn MrvAssignmentSelectsAZeroConflictAlternative() {
        let Candidate = |Id: &str, Wire: usize, Electrical: &[usize]| AssignmentCandidate {
            CandidateId: Id.to_string(),
            OwnerSignal: Id[..1].to_string(),
            TemplateKey: String::new(),
            Claims: std::sync::Arc::new(
                ClaimMask::FromIndices(16, &[Wire], &[], &[], Electrical).unwrap(),
            ),
            MaterialCost: 1,
            FootprintGrowth: 1,
            Length: 1,
            BendCount: 0,
            ViaCount: 0,
        };
        let Groups = BTreeMap::from([
            ("A".to_string(), vec![Candidate("A0", 2, &[1, 2, 3])]),
            (
                "B".to_string(),
                vec![
                    Candidate("B0", 3, &[2, 3, 4]),
                    Candidate("B1", 8, &[7, 8, 9]),
                ],
            ),
        ]);
        let mut Selected = Vec::new();
        let mut Expansions = 0;
        let mut BudgetExhausted = false;
        let mut Failure = None;
        let mut ConflictSignals = Vec::new();
        let mut Conflicts = Vec::new();
        assert!(AssignCandidates(
            &Groups,
            &["A".to_string(), "B".to_string()],
            &ClaimMask::New(16),
            &BTreeMap::new(),
            &mut Selected,
            &mut Expansions,
            16,
            &mut BudgetExhausted,
            &RuntimeDeadline::Unlimited(),
            &mut Failure,
            &mut ConflictSignals,
            &mut Conflicts,
            &mut Vec::new(),
            &mut false,
        ));
        assert!(!BudgetExhausted);
        assert!(Selected.contains(&("B".to_string(), "B1".to_string())));
    }

    #[test]
    fn AssignmentBudgetHardFails() {
        let Candidate = AssignmentCandidate {
            CandidateId: "A0".to_string(),
            OwnerSignal: "A".to_string(),
            TemplateKey: String::new(),
            Claims: std::sync::Arc::new(ClaimMask::FromIndices(4, &[0], &[], &[], &[0]).unwrap()),
            MaterialCost: 1,
            FootprintGrowth: 1,
            Length: 1,
            BendCount: 0,
            ViaCount: 0,
        };
        let Groups = BTreeMap::from([("A".to_string(), vec![Candidate])]);
        let mut Selected = Vec::new();
        let mut Expansions = 0;
        let mut BudgetExhausted = false;
        let mut Failure = None;
        let mut ConflictSignals = Vec::new();
        let mut Conflicts = Vec::new();
        assert!(!AssignCandidates(
            &Groups,
            &["A".to_string()],
            &ClaimMask::New(4),
            &BTreeMap::new(),
            &mut Selected,
            &mut Expansions,
            0,
            &mut BudgetExhausted,
            &RuntimeDeadline::Unlimited(),
            &mut Failure,
            &mut ConflictSignals,
            &mut Conflicts,
            &mut Vec::new(),
            &mut false,
        ));
        assert!(BudgetExhausted);
        assert_eq!(Failure, Some("A".to_string()));
    }

    #[test]
    fn ExhaustiveAssignmentFailureIsNotABudgetFailure() {
        let Candidate = |Id: &str| AssignmentCandidate {
            CandidateId: Id.to_string(),
            OwnerSignal: Id[..1].to_string(),
            TemplateKey: String::new(),
            Claims: std::sync::Arc::new(
                ClaimMask::FromIndices(4, &[1], &[], &[], &[0, 1, 2]).unwrap(),
            ),
            MaterialCost: 1,
            FootprintGrowth: 1,
            Length: 1,
            BendCount: 0,
            ViaCount: 0,
        };
        let Groups = BTreeMap::from([
            ("A".to_string(), vec![Candidate("A0")]),
            ("B".to_string(), vec![Candidate("B0")]),
        ]);
        let mut Selected = Vec::new();
        let mut Expansions = 0;
        let mut BudgetExhausted = false;
        let mut Failure = None;
        let mut ConflictSignals = Vec::new();
        let mut Conflicts = Vec::new();
        assert!(!AssignCandidates(
            &Groups,
            &["A".to_string(), "B".to_string()],
            &ClaimMask::New(4),
            &BTreeMap::new(),
            &mut Selected,
            &mut Expansions,
            128,
            &mut BudgetExhausted,
            &RuntimeDeadline::Unlimited(),
            &mut Failure,
            &mut ConflictSignals,
            &mut Conflicts,
            &mut Vec::new(),
            &mut false,
        ));
        assert!(!BudgetExhausted);
        assert_eq!(Expansions, 1);
        assert_eq!(Failure, Some("B".to_string()));
        assert_eq!(ConflictSignals, vec!["A".to_string(), "B".to_string()]);
        assert!(!Conflicts.is_empty());
    }

    #[test]
    fn AssignmentRespectsPreOwnedBaseClaims() {
        let Candidate = |Id: &str, Wire: usize, Electrical: &[usize]| AssignmentCandidate {
            CandidateId: Id.to_string(),
            OwnerSignal: "Extension".to_string(),
            TemplateKey: String::new(),
            Claims: std::sync::Arc::new(
                ClaimMask::FromIndices(16, &[Wire], &[], &[], Electrical).unwrap(),
            ),
            MaterialCost: 1,
            FootprintGrowth: 1,
            Length: 1,
            BendCount: 0,
            ViaCount: 0,
        };
        let Groups = BTreeMap::from([(
            "Extension".to_string(),
            vec![
                Candidate("blocked", 3, &[2, 3, 4]),
                Candidate("clear", 10, &[9, 10, 11]),
            ],
        )]);
        let Base = ClaimMask::FromIndices(16, &[2], &[], &[], &[1, 2, 3]).unwrap();
        let mut Selected = Vec::new();
        let mut Expansions = 0;
        let mut BudgetExhausted = false;
        let mut Failure = None;
        let mut ConflictSignals = Vec::new();
        let mut Conflicts = Vec::new();
        assert!(AssignCandidates(
            &Groups,
            &["Extension".to_string()],
            &ClaimMask::New(16),
            &BTreeMap::from([("Base".to_string(), Base)]),
            &mut Selected,
            &mut Expansions,
            16,
            &mut BudgetExhausted,
            &RuntimeDeadline::Unlimited(),
            &mut Failure,
            &mut ConflictSignals,
            &mut Conflicts,
            &mut Vec::new(),
            &mut false,
        ));
        assert!(!BudgetExhausted);
        assert_eq!(
            Selected,
            vec![("Extension".to_string(), "clear".to_string())]
        );
    }

    #[test]
    fn AssignmentMergesSameSignalBaseClaims() {
        let Candidate = AssignmentCandidate {
            CandidateId: "extension".to_string(),
            OwnerSignal: "Signal".to_string(),
            TemplateKey: String::new(),
            Claims: std::sync::Arc::new(
                ClaimMask::FromIndices(16, &[3], &[], &[], &[2, 3, 4]).unwrap(),
            ),
            MaterialCost: 1,
            FootprintGrowth: 1,
            Length: 1,
            BendCount: 0,
            ViaCount: 0,
        };
        let Groups = BTreeMap::from([("Signal".to_string(), vec![Candidate])]);
        let Base = ClaimMask::FromIndices(16, &[2], &[], &[], &[1, 2, 3]).unwrap();
        let mut Selected = Vec::new();
        let mut Expansions = 0;
        let mut BudgetExhausted = false;
        let mut Failure = None;
        let mut ConflictSignals = Vec::new();
        let mut Conflicts = Vec::new();
        assert!(AssignCandidates(
            &Groups,
            &["Signal".to_string()],
            &ClaimMask::New(16),
            &BTreeMap::from([("Signal".to_string(), Base)]),
            &mut Selected,
            &mut Expansions,
            16,
            &mut BudgetExhausted,
            &RuntimeDeadline::Unlimited(),
            &mut Failure,
            &mut ConflictSignals,
            &mut Conflicts,
            &mut Vec::new(),
            &mut false,
        ));
        assert!(!BudgetExhausted);
        assert_eq!(
            Selected,
            vec![("Signal".to_string(), "extension".to_string())]
        );
    }

    #[test]
    fn RectilinearTopologyIsDeterministicAndAxisAligned() {
        let First = GenerateRectilinearTopology(vec![(4, 4), (0, 0), (4, 0)]);
        let Second = GenerateRectilinearTopology(vec![(4, 0), (4, 4), (0, 0)]);
        assert_eq!(First, Second);
        assert!(First.iter().all(|(A, B)| A.0 == B.0 || A.1 == B.1));
    }
}
