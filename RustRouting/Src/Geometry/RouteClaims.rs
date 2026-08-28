//! Route topology and deterministic physical-claim construction.

use crate::Core::Models::{Edge, Position, Position2, RectilinearEdge};
use crate::Core::Runtime::RoutingThreadPool;
use pyo3::prelude::*;
use std::collections::BTreeSet;
use std::sync::Arc;

type RouteClaimValues = (Vec<Position>, Vec<Position>, Vec<Position>, Vec<Position>);
type DeferredRouteClaimValues = (Vec<Position>, bool);

#[pyfunction]
pub(crate) fn GenerateRectilinearTopology(TerminalValues: Vec<Position2>) -> Vec<RectilinearEdge> {
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

pub(super) fn NormalizeEdge(First: Position, Second: Position) -> Edge {
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

fn BuildDeferredRouteClaimValues(Values: Vec<Position>) -> DeferredRouteClaimValues {
    let Wire = Values.into_iter().collect::<BTreeSet<_>>();
    let mut Air = BTreeSet::new();
    for PositionValue in &Wire {
        let (_, Y, _) = *PositionValue;
        for Neighbor in NeighborPositions(*PositionValue) {
            if Neighbor <= *PositionValue || Neighbor.1 == Y || !Wire.contains(&Neighbor) {
                continue;
            }
            let Lower = if Y < Neighbor.1 {
                *PositionValue
            } else {
                Neighbor
            };
            Air.insert((Lower.0, Lower.1 + 1, Lower.2));
        }
    }
    let SelfLegal = Air.is_disjoint(&Wire)
        && Wire.iter().all(|(X, Y, Z)| {
            let Support = (*X, Y - 1, *Z);
            !Wire.contains(&Support) && !Air.contains(&Support)
        });
    (Air.into_iter().collect(), SelfLegal)
}

/// Build only the non-derivable vertical-air part of deferred compact claims.
/// Wire, support, and electrical claims remain path-derived and are expanded
/// by the compact solver only for an attempted member.
fn BuildDeferredRouteClaimsBatchNative(
    NodeSets: Vec<Vec<Position>>,
) -> (Vec<DeferredRouteClaimValues>, usize) {
    let Pool = RoutingThreadPool();
    let WorkerCount = Pool.current_num_threads().min(NodeSets.len());
    if WorkerCount < 2 {
        return (
            NodeSets
                .into_iter()
                .map(BuildDeferredRouteClaimValues)
                .collect(),
            WorkerCount,
        );
    }
    let SharedNodes = Arc::new(NodeSets);
    let Shards = Pool.broadcast(|Context| {
        let WorkerIndex = Context.index();
        let WorkerTotal = Context.num_threads();
        let Values = (WorkerIndex..SharedNodes.len())
            .step_by(WorkerTotal)
            .map(|Index| {
                (
                    Index,
                    BuildDeferredRouteClaimValues(SharedNodes[Index].clone()),
                )
            })
            .collect::<Vec<_>>();
        (WorkerIndex, Values)
    });
    let mut Ordered = (0..SharedNodes.len())
        .map(|_| None)
        .collect::<Vec<Option<DeferredRouteClaimValues>>>();
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
            .map(|Value| Value.expect("every deferred claim batch item needs one worker"))
            .collect(),
        ActiveWorkers,
    )
}

#[pyfunction]
pub(crate) fn BuildRouteClaimsBatch(
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
pub(crate) fn BuildRouteClaimsBatchWithTelemetry(
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
pub(crate) fn BuildDeferredRouteClaimsBatchWithTelemetry(
    PythonValue: Python<'_>,
    NodeSets: Vec<Vec<Position>>,
) -> (Vec<DeferredRouteClaimValues>, usize) {
    PythonValue.allow_threads(|| BuildDeferredRouteClaimsBatchNative(NodeSets))
}
