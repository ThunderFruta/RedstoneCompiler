#![allow(non_snake_case)]

mod Assignment;
mod AssignmentPlanning;
mod Bindings;
mod Deadline;
mod Generation;
mod Models;
mod PathRouting;

use Models::*;

use pyo3::prelude::*;
use rayon::prelude::*;
use rayon::{ThreadPool, ThreadPoolBuilder};
use std::sync::OnceLock;

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
            .unwrap_or(Available.min(16));
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
