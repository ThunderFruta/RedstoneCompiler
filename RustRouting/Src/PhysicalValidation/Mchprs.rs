//! MCHPRS Redpiler-backed exhaustive physical validation.

use mchprs_blocks::blocks::Block;
use mchprs_blocks::BlockPos;
use mchprs_redpiler::{Compiler, CompilerOptions, TaskMonitor};
use mchprs_world::testing::TestWorld;
use mchprs_world::World;
use pyo3::prelude::*;
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;

const BACKEND_NAME: &str = "mchprs-redpiler-fe217210";

#[derive(Deserialize)]
struct FixtureBlockState {
    Name: String,
    #[serde(default)]
    Properties: HashMap<String, String>,
}

#[derive(Deserialize)]
struct FixtureBlock {
    Position: [i32; 3],
    State: FixtureBlockState,
}

#[derive(Deserialize)]
struct FixtureInput {
    Name: String,
    LeverPosition: [i32; 3],
}

#[derive(Deserialize)]
struct FixtureOutput {
    Name: String,
    LampPosition: [i32; 3],
}

#[derive(Deserialize)]
struct PhysicalFixtureDocument {
    Blocks: Vec<FixtureBlock>,
    Inputs: Vec<FixtureInput>,
    Outputs: Vec<FixtureOutput>,
}

#[derive(Deserialize)]
struct LogicGateDocument {
    Name: String,
    Kind: String,
    Inputs: Vec<String>,
    Outputs: Vec<String>,
}

#[derive(Deserialize)]
struct LogicDocument {
    Inputs: Vec<String>,
    Outputs: Vec<String>,
    Gates: Vec<LogicGateDocument>,
}

enum LogicOperationKind {
    Nand,
    And,
    Or,
    Xor,
    Not,
    Buffer,
}

struct LogicOperation {
    Kind: LogicOperationKind,
    Inputs: Vec<usize>,
    Outputs: Vec<usize>,
}

struct CompiledLogic {
    InputSignals: Vec<usize>,
    OutputSignals: Vec<(String, usize)>,
    Operations: Vec<LogicOperation>,
    SignalCount: usize,
}

impl CompiledLogic {
    fn Build(
        Document: LogicDocument,
        CanonicalInputs: &[String],
        CanonicalOutputs: &[String],
    ) -> Result<Self, String> {
        let mut SignalIndices = HashMap::new();
        for Name in Document.Inputs.iter().chain(Document.Outputs.iter()) {
            let NextIndex = SignalIndices.len();
            SignalIndices.entry(Name.clone()).or_insert(NextIndex);
        }
        for Gate in &Document.Gates {
            for Name in Gate.Inputs.iter().chain(Gate.Outputs.iter()) {
                let NextIndex = SignalIndices.len();
                SignalIndices.entry(Name.clone()).or_insert(NextIndex);
            }
        }

        let InputSignals = CanonicalInputs
            .iter()
            .map(|Name| {
                SignalIndices
                    .get(Name)
                    .copied()
                    .ok_or_else(|| format!("logic document is missing input {Name}"))
            })
            .collect::<Result<Vec<_>, _>>()?;
        let OutputSignals = CanonicalOutputs
            .iter()
            .map(|Name| {
                SignalIndices
                    .get(Name)
                    .copied()
                    .map(|Index| (Name.clone(), Index))
                    .ok_or_else(|| format!("logic document is missing output {Name}"))
            })
            .collect::<Result<Vec<_>, _>>()?;

        let mut Produced = vec![false; SignalIndices.len()];
        for Name in &Document.Inputs {
            Produced[SignalIndices[Name]] = true;
        }
        let mut Operations = Vec::new();
        for Gate in Document.Gates {
            if Gate.Kind == "INPUT" {
                continue;
            }
            let Inputs = Gate
                .Inputs
                .iter()
                .map(|Name| {
                    let Index = SignalIndices[Name];
                    if !Produced[Index] {
                        Err(format!(
                            "logic gate {} reads unresolved signal {Name}",
                            Gate.Name
                        ))
                    } else {
                        Ok(Index)
                    }
                })
                .collect::<Result<Vec<_>, _>>()?;
            let Outputs = Gate
                .Outputs
                .iter()
                .map(|Name| SignalIndices[Name])
                .collect::<Vec<_>>();
            let Kind = match Gate.Kind.as_str() {
                "NAND" => LogicOperationKind::Nand,
                "AND" => LogicOperationKind::And,
                "OR" => LogicOperationKind::Or,
                "XOR" => LogicOperationKind::Xor,
                "NOT" => LogicOperationKind::Not,
                "BUFFER" | "OUTPUT" => LogicOperationKind::Buffer,
                Other => return Err(format!("unsupported logic gate kind {Other}")),
            };
            for Output in &Outputs {
                Produced[*Output] = true;
            }
            Operations.push(LogicOperation {
                Kind,
                Inputs,
                Outputs,
            });
        }
        Ok(Self {
            SignalCount: SignalIndices.len(),
            InputSignals,
            OutputSignals,
            Operations,
        })
    }

    fn Evaluate(&self, Assignment: u64) -> Vec<(String, bool)> {
        let mut Values = vec![false; self.SignalCount];
        for (BitIndex, SignalIndex) in self.InputSignals.iter().enumerate() {
            Values[*SignalIndex] = Assignment & (1_u64 << BitIndex) != 0;
        }
        for Operation in &self.Operations {
            let Result = match Operation.Kind {
                LogicOperationKind::Nand => !Operation.Inputs.iter().all(|Index| Values[*Index]),
                LogicOperationKind::And => Operation.Inputs.iter().all(|Index| Values[*Index]),
                LogicOperationKind::Or => Operation.Inputs.iter().any(|Index| Values[*Index]),
                LogicOperationKind::Xor => {
                    Operation
                        .Inputs
                        .iter()
                        .filter(|Index| Values[**Index])
                        .count()
                        % 2
                        == 1
                }
                LogicOperationKind::Not => !Values[Operation.Inputs[0]],
                LogicOperationKind::Buffer => Values[Operation.Inputs[0]],
            };
            for Output in &Operation.Outputs {
                Values[*Output] = Result;
            }
        }
        self.OutputSignals
            .iter()
            .map(|(Name, Index)| (Name.clone(), Values[*Index]))
            .collect()
    }
}

struct SimulationWorld {
    World: TestWorld,
    Offset: BlockPos,
    Minimum: BlockPos,
    Maximum: BlockPos,
}

impl SimulationWorld {
    fn Position(&self, Position: [i32; 3]) -> BlockPos {
        BlockPos::new(
            Position[0] + self.Offset.x,
            Position[1] + self.Offset.y,
            Position[2] + self.Offset.z,
        )
    }
}

fn BuildWorld(Fixture: &PhysicalFixtureDocument) -> Result<SimulationWorld, String> {
    if Fixture.Blocks.is_empty() {
        return Err("physical fixture contains no blocks".to_string());
    }
    let MinimumRaw = Fixture
        .Blocks
        .iter()
        .fold([i32::MAX; 3], |mut Current, Block| {
            for Axis in 0..3 {
                Current[Axis] = Current[Axis].min(Block.Position[Axis]);
            }
            Current
        });
    let MaximumRaw = Fixture
        .Blocks
        .iter()
        .fold([i32::MIN; 3], |mut Current, Block| {
            for Axis in 0..3 {
                Current[Axis] = Current[Axis].max(Block.Position[Axis]);
            }
            Current
        });
    let Offset = BlockPos::new(1 - MinimumRaw[0], 1 - MinimumRaw[1], 1 - MinimumRaw[2]);
    let Minimum = BlockPos::new(1, 1, 1);
    let Maximum = BlockPos::new(
        MaximumRaw[0] + Offset.x,
        MaximumRaw[1] + Offset.y,
        MaximumRaw[2] + Offset.z,
    );
    let XChunks = (Maximum.x + 2).div_euclid(16) + 1;
    let YSections = (Maximum.y + 2).div_euclid(16) + 1;
    let ZChunks = (Maximum.z + 2).div_euclid(16) + 1;
    let mut WorldValue = TestWorld::new(XChunks, YSections, ZChunks);
    let mut PlacedPositions = Vec::with_capacity(Fixture.Blocks.len());
    for FixtureBlock { Position, State } in &Fixture.Blocks {
        let CompatibilityName = match State.Name.as_str() {
            "minecraft:smooth_stone" => "minecraft:stone",
            Other => Other,
        };
        let mut BlockValue = Block::from_name(CompatibilityName)
            .ok_or_else(|| format!("MCHPRS does not support fixture block {}", State.Name))?;
        let Properties = State
            .Properties
            .iter()
            .map(|(Name, Value)| (Name.as_str(), Value.as_str()))
            .collect::<HashMap<_, _>>();
        BlockValue.set_properties(Properties);
        let Translated = BlockPos::new(
            Position[0] + Offset.x,
            Position[1] + Offset.y,
            Position[2] + Offset.z,
        );
        if !WorldValue.set_block(Translated, BlockValue) {
            return Err(format!("could not place fixture block at {Position:?}"));
        }
        PlacedPositions.push(Translated);
    }
    InitializeVanillaRedstone(&mut WorldValue, &PlacedPositions, 200)?;
    Ok(SimulationWorld {
        World: WorldValue,
        Offset,
        Minimum,
        Maximum,
    })
}

fn InitializeVanillaRedstone(
    WorldValue: &mut TestWorld,
    Positions: &[BlockPos],
    MaximumTicks: usize,
) -> Result<(), String> {
    for Position in Positions {
        let BlockValue = WorldValue.get_block(*Position);
        mchprs_redstone::update(BlockValue, WorldValue, *Position);
    }
    let mut Ticks = 0;
    while !WorldValue.to_be_ticked.is_empty() {
        if Ticks >= MaximumTicks {
            return Err(format!(
                "MCHPRS fixture initialization exceeded {MaximumTicks} redstone ticks"
            ));
        }
        WorldValue
            .to_be_ticked
            .sort_by_key(|Entry| (Entry.ticks_left, Entry.tick_priority));
        for Pending in &mut WorldValue.to_be_ticked {
            Pending.ticks_left = Pending.ticks_left.saturating_sub(1);
        }
        while WorldValue
            .to_be_ticked
            .first()
            .is_some_and(|Entry| Entry.ticks_left == 0)
        {
            let Entry = WorldValue.to_be_ticked.remove(0);
            let BlockValue = WorldValue.get_block(Entry.pos);
            mchprs_redstone::tick(BlockValue, WorldValue, Entry.pos);
        }
        Ticks += 1;
    }
    Ok(())
}

fn Settle(
    CompilerValue: &mut Compiler,
    WorldValue: &mut TestWorld,
    MaximumTicks: usize,
) -> Result<usize, usize> {
    let mut Ticks = 0;
    while CompilerValue.has_pending_ticks() {
        if Ticks >= MaximumTicks {
            return Err(Ticks);
        }
        CompilerValue.tick();
        Ticks += 1;
    }
    CompilerValue.flush(WorldValue);
    Ok(Ticks)
}

fn AssignmentObject(InputNames: &[String], Assignment: u64) -> Value {
    let mut Result = Map::new();
    for (Index, Name) in InputNames.iter().enumerate() {
        Result.insert(
            Name.clone(),
            Value::Bool(Assignment & (1_u64 << Index) != 0),
        );
    }
    Value::Object(Result)
}

#[pyfunction]
#[pyo3(signature=(FixtureJson, LogicJson, ExhaustiveInputLimit=20, MaximumSettleTicks=100, WideSampleAssignments=Vec::new(), ProgressCallback=None))]
pub fn ValidateMchprsFixture(
    FixtureJson: &str,
    LogicJson: &str,
    ExhaustiveInputLimit: usize,
    MaximumSettleTicks: usize,
    WideSampleAssignments: Vec<u64>,
    ProgressCallback: Option<PyObject>,
) -> PyResult<String> {
    let Started = Instant::now();
    if ExhaustiveInputLimit > 20 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "exhaustive input limit cannot exceed 20",
        ));
    }
    if MaximumSettleTicks == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "maximum settle ticks must be positive",
        ));
    }
    let Fixture: PhysicalFixtureDocument = serde_json::from_str(FixtureJson).map_err(|Error| {
        pyo3::exceptions::PyValueError::new_err(format!("invalid physical fixture: {Error}"))
    })?;
    let LogicDocument: LogicDocument = serde_json::from_str(LogicJson).map_err(|Error| {
        pyo3::exceptions::PyValueError::new_err(format!("invalid logic document: {Error}"))
    })?;
    let mut Inputs = Fixture
        .Inputs
        .iter()
        .map(|Value| Value.Name.clone())
        .collect::<Vec<_>>();
    Inputs.sort();
    let mut Outputs = Fixture
        .Outputs
        .iter()
        .map(|Value| Value.Name.clone())
        .collect::<Vec<_>>();
    Outputs.sort();
    if Inputs.len() > 63 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "MCHPRS validation supports at most 63 inputs",
        ));
    }
    let Logic = CompiledLogic::Build(LogicDocument, &Inputs, &Outputs)
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let mut Simulation = BuildWorld(&Fixture).map_err(pyo3::exceptions::PyValueError::new_err)?;
    let InputPositions = Inputs
        .iter()
        .map(|Name| {
            let Input = Fixture
                .Inputs
                .iter()
                .find(|Value| &Value.Name == Name)
                .unwrap();
            (Name.clone(), Simulation.Position(Input.LeverPosition))
        })
        .collect::<Vec<_>>();
    let OutputPositions = Outputs
        .iter()
        .map(|Name| {
            let Output = Fixture
                .Outputs
                .iter()
                .find(|Value| &Value.Name == Name)
                .unwrap();
            (Name.clone(), Simulation.Position(Output.LampPosition))
        })
        .collect::<Vec<_>>();

    let GraphStarted = Instant::now();
    let mut CompilerValue = Compiler::default();
    CompilerValue.compile(
        &Simulation.World,
        (Simulation.Minimum, Simulation.Maximum),
        CompilerOptions {
            io_only: true,
            ..Default::default()
        },
        Vec::new(),
        Arc::new(TaskMonitor::default()),
    );
    let GraphCompileSeconds = GraphStarted.elapsed().as_secs_f64();

    for (_, Position) in &InputPositions {
        CompilerValue.on_use_block(*Position);
    }
    Settle(
        &mut CompilerValue,
        &mut Simulation.World,
        MaximumSettleTicks,
    )
    .map_err(|_| {
        pyo3::exceptions::PyRuntimeError::new_err("MCHPRS warmup timed out with all inputs enabled")
    })?;
    for (_, Position) in &InputPositions {
        CompilerValue.on_use_block(*Position);
    }
    Settle(
        &mut CompilerValue,
        &mut Simulation.World,
        MaximumSettleTicks,
    )
    .map_err(|_| {
        pyo3::exceptions::PyRuntimeError::new_err(
            "MCHPRS warmup timed out returning inputs to zero",
        )
    })?;

    let Exhaustive = Inputs.len() <= ExhaustiveInputLimit;
    let Total = if Exhaustive {
        1_usize << Inputs.len()
    } else {
        WideSampleAssignments.len()
    };
    if Total == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "validation assignment set is empty",
        ));
    }
    let SimulationStarted = Instant::now();
    let mut PreviousAssignment = 0_u64;
    let mut MaximumObservedSettleTicks = 0_usize;
    for ExecutionIndex in 0..Total {
        let Assignment = if Exhaustive {
            let Index = ExecutionIndex as u64;
            Index ^ (Index >> 1)
        } else {
            WideSampleAssignments[ExecutionIndex]
        };
        let Changed = PreviousAssignment ^ Assignment;
        for BitIndex in 0..Inputs.len() {
            if Changed & (1_u64 << BitIndex) != 0 {
                CompilerValue.on_use_block(InputPositions[BitIndex].1);
            }
        }
        let SettleTicks = match Settle(
            &mut CompilerValue,
            &mut Simulation.World,
            MaximumSettleTicks,
        ) {
            Ok(Value) => Value,
            Err(Value) => {
                return Ok(json!({
                    "Status": "timeout",
                    "Backend": BACKEND_NAME,
                    "RuntimeSeconds": Started.elapsed().as_secs_f64(),
                    "Diagnostics": {
                        "TestedVectors": ExecutionIndex,
                        "TotalVectors": Total,
                        "ExecutionIndex": ExecutionIndex,
                        "AssignmentIndex": Assignment,
                        "Inputs": AssignmentObject(&Inputs, Assignment),
                        "SettleTicks": Value,
                        "MaximumSettleTicks": MaximumSettleTicks,
                        "GraphCompileSeconds": GraphCompileSeconds
                    }
                })
                .to_string());
            }
        };
        MaximumObservedSettleTicks = MaximumObservedSettleTicks.max(SettleTicks);
        let Expected = Logic.Evaluate(Assignment);
        let mut Actual = Vec::with_capacity(OutputPositions.len());
        for (Name, Position) in &OutputPositions {
            let Lit = match Simulation.World.get_block(*Position) {
                Block::RedstoneLamp { lit } => lit,
                Other => {
                    return Err(pyo3::exceptions::PyRuntimeError::new_err(format!(
                        "output {Name} is not a lamp after simulation: {Other:?}"
                    )))
                }
            };
            Actual.push((Name.clone(), Lit));
        }
        if Actual != Expected {
            let ExpectedObject = Expected
                .iter()
                .map(|(Name, Value)| (Name.clone(), Value::Bool(*Value)))
                .collect::<Map<_, _>>();
            let ActualObject = Actual
                .iter()
                .map(|(Name, Value)| (Name.clone(), Value::Bool(*Value)))
                .collect::<Map<_, _>>();
            let FirstMismatch = Expected
                .iter()
                .zip(Actual.iter())
                .find(|(ExpectedValue, ActualValue)| ExpectedValue.1 != ActualValue.1)
                .unwrap();
            let FailedOutput = Fixture
                .Outputs
                .iter()
                .find(|Output| Output.Name == FirstMismatch.0 .0)
                .unwrap();
            return Ok(json!({
                "Status": "mismatch",
                "Backend": BACKEND_NAME,
                "RuntimeSeconds": Started.elapsed().as_secs_f64(),
                "Diagnostics": {
                    "TestedVectors": ExecutionIndex,
                    "TotalVectors": Total,
                    "ExecutionIndex": ExecutionIndex,
                    "AssignmentIndex": Assignment,
                    "Inputs": AssignmentObject(&Inputs, Assignment),
                    "Expected": ExpectedObject,
                    "Actual": ActualObject,
                    "Mismatch": {
                        "Output": FirstMismatch.0.0,
                        "Expected": FirstMismatch.0.1,
                        "Actual": FirstMismatch.1.1,
                        "LampPosition": FailedOutput.LampPosition,
                        "LampBlock": format!(
                            "{:?}",
                            Simulation.World.get_block(
                                Simulation.Position(FailedOutput.LampPosition)
                            )
                        )
                    },
                    "SettleTicks": SettleTicks,
                    "MaximumSettleTicks": MaximumSettleTicks,
                    "GraphCompileSeconds": GraphCompileSeconds
                }
            })
            .to_string());
        }
        PreviousAssignment = Assignment;
        if let Some(Callback) = &ProgressCallback {
            if (ExecutionIndex + 1) % 1024 == 0 || ExecutionIndex + 1 == Total {
                Python::with_gil(|PythonValue| {
                    Callback.call1(PythonValue, (ExecutionIndex + 1, Total))
                })?;
            }
        }
    }
    let SimulationSeconds = SimulationStarted.elapsed().as_secs_f64();
    Ok(json!({
        "Status": "passed",
        "Backend": BACKEND_NAME,
        "RuntimeSeconds": Started.elapsed().as_secs_f64(),
        "Diagnostics": {
            "TestedVectors": Total,
            "TotalVectors": Total,
            "InputCount": Inputs.len(),
            "OutputCount": Outputs.len(),
            "Exhaustive": Exhaustive,
            "ExhaustiveInputLimit": ExhaustiveInputLimit,
            "ExecutionOrder": if Exhaustive { "gray-code" } else { "deterministic-sample" },
            "GraphCompileSeconds": GraphCompileSeconds,
            "SimulationSeconds": SimulationSeconds,
            "VectorsPerSecond": Total as f64 / SimulationSeconds.max(f64::EPSILON),
            "MaximumSettleTicks": MaximumObservedSettleTicks,
            "SettleTickCeiling": MaximumSettleTicks
        }
    })
    .to_string())
}
