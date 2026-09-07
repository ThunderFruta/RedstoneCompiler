//! Isolated, expectation-free per-tick physical observations.

use mchprs_blocks::blocks::Block;
use mchprs_blocks::BlockPos;
use mchprs_redpiler::{Compiler, CompilerOptions, TaskMonitor};
use mchprs_world::testing::TestWorld;
use mchprs_world::World;
use pyo3::prelude::*;
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::Arc;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct State {
    Name: String,
    #[serde(default)]
    Properties: BTreeMap<String, String>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FixtureBlock {
    Position: [i32; 3],
    State: State,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Port {
    Name: String,
    Position: [i32; 3],
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Fixture {
    Blocks: Vec<FixtureBlock>,
    Inputs: Vec<Port>,
    Outputs: Vec<Port>,
    RouteInputs: BTreeMap<String, [i32; 3]>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    Fixture: Fixture,
    InitialInputs: BTreeMap<String, bool>,
    AppliedInputs: BTreeMap<String, bool>,
    HorizonTicks: usize,
    InitializationTicks: usize,
}

fn ReadBoolean(WorldValue: &TestWorld, Position: BlockPos) -> Result<bool, String> {
    match WorldValue.get_block(Position) {
        Block::Lever { powered, .. } => Ok(powered),
        Block::RedstoneLamp { lit } => Ok(lit),
        Block::RedstoneWire(Wire) => Ok(Wire.power > 0),
        Block::Repeater(Repeater) => Ok(Repeater.powered),
        Other => Err(format!("unsupported-observation-block: {Other:?}")),
    }
}

fn Run(RequestValue: Request) -> Result<Value, String> {
    let Request {
        Fixture,
        InitialInputs,
        AppliedInputs,
        HorizonTicks,
        InitializationTicks,
    } = RequestValue;
    if Fixture.Blocks.is_empty()
        || Fixture.Blocks.len() > 100000
        || HorizonTicks > 100000
        || InitializationTicks == 0
        || InitializationTicks > 10000
    {
        return Err("invalid-size-or-tick-bound".into());
    }
    let Names: BTreeSet<_> = Fixture.Inputs.iter().map(|P| P.Name.clone()).collect();
    if Names.len() != Fixture.Inputs.len()
        || Names != InitialInputs.keys().cloned().collect()
        || Names != AppliedInputs.keys().cloned().collect()
    {
        return Err("complete-unique-input-vectors-required".into());
    }
    if !Fixture.RouteInputs.is_empty() && Names != Fixture.RouteInputs.keys().cloned().collect() {
        return Err("complete-route-input-map-required".into());
    }
    let mut Seen = BTreeSet::new();
    let mut Minimum = [i32::MAX; 3];
    let mut Maximum = [i32::MIN; 3];
    for Item in &Fixture.Blocks {
        if !Seen.insert(Item.Position) || Item.Position.iter().any(|V| V.unsigned_abs() > 1000000) {
            return Err("duplicate-or-out-of-range-position".into());
        }
        for Axis in 0..3 {
            Minimum[Axis] = Minimum[Axis].min(Item.Position[Axis]);
            Maximum[Axis] = Maximum[Axis].max(Item.Position[Axis]);
        }
    }
    let Extents = [
        Maximum[0] - Minimum[0] + 3,
        Maximum[1] - Minimum[1] + 3,
        Maximum[2] - Minimum[2] + 3,
    ];
    if Extents.iter().any(|V| *V > 512)
        || Extents.iter().map(|V| i64::from(*V)).product::<i64>() > 2000000
    {
        return Err("fixture-volume-out-of-range".into());
    }
    let Translate = |P: [i32; 3]| {
        BlockPos::new(
            P[0] - Minimum[0] + 1,
            P[1] - Minimum[1] + 1,
            P[2] - Minimum[2] + 1,
        )
    };
    let mut WorldValue = TestWorld::new(
        (Extents[0] + 15) / 16,
        (Extents[1] + 15) / 16,
        (Extents[2] + 15) / 16,
    );
    let mut InputPositions = BTreeSet::new();
    let mut OutputPositions = BTreeSet::new();
    let mut OutputNames = BTreeSet::new();
    for PortValue in &Fixture.Inputs {
        if PortValue.Name.is_empty()
            || !InputPositions.insert(PortValue.Position)
            || !Seen.contains(&PortValue.Position)
        {
            return Err("invalid-input-port".into());
        }
    }
    for PortValue in &Fixture.Outputs {
        if PortValue.Name.is_empty()
            || !OutputPositions.insert(PortValue.Position)
            || !OutputNames.insert(&PortValue.Name)
            || !Seen.contains(&PortValue.Position)
        {
            return Err("invalid-output-port".into());
        }
    }
    for Item in &Fixture.Blocks {
        let mut BlockValue = Block::from_name(&Item.State.Name)
            .ok_or_else(|| format!("unsupported-block: {}", Item.State.Name))?;
        BlockValue.set_properties(
            Item.State
                .Properties
                .iter()
                .map(|(K, V)| (K.as_str(), V.as_str()))
                .collect::<HashMap<_, _>>(),
        );
        if matches!(BlockValue, Block::Repeater(R) if !(1..=4).contains(&R.delay)) {
            return Err("unsupported-block-properties: repeater delay must be 1..4".into());
        }
        if matches!(BlockValue, Block::RedstoneWire(W) if W.power > 15) {
            return Err("unsupported-block-properties: wire power must be 0..15".into());
        }
        // Reject ignored or invalid properties instead of simulating another state.
        let ActualProperties = BlockValue.properties();
        if Item
            .State
            .Properties
            .iter()
            .any(|(K, V)| ActualProperties.get(K.as_str()) != Some(V))
        {
            return Err(format!("unsupported-block-properties: {}", Item.State.Name));
        }
        if let Some(Input) = Fixture.Inputs.iter().find(|P| P.Position == Item.Position) {
            match &mut BlockValue {
                Block::Lever { powered, .. } => *powered = InitialInputs[&Input.Name],
                _ => return Err("unsupported-control-block".into()),
            }
        }
        WorldValue.set_block(Translate(Item.Position), BlockValue);
    }
    for P in Fixture.RouteInputs.values() {
        if !Seen.contains(P)
            || !matches!(WorldValue.get_block(Translate(*P)), Block::RedstoneWire(_))
        {
            return Err("route-input-root-must-be-dust".into());
        }
    }
    for P in &Fixture.Outputs {
        ReadBoolean(&WorldValue, Translate(P.Position))?;
    }
    // Initialize the complete declared baseline before creating the compiler.
    for Item in &Fixture.Blocks {
        let P = Translate(Item.Position);
        let B = WorldValue.get_block(P);
        mchprs_redstone::update(B, &mut WorldValue, P);
    }
    let mut InitializationElapsed = 0;
    while !WorldValue.to_be_ticked.is_empty() {
        if InitializationElapsed >= InitializationTicks {
            return Err("initialization-timeout".into());
        }
        for Entry in &mut WorldValue.to_be_ticked {
            Entry.ticks_left = Entry.ticks_left.saturating_sub(1);
        }
        WorldValue
            .to_be_ticked
            .sort_by_key(|E| (E.ticks_left, E.tick_priority));
        while WorldValue
            .to_be_ticked
            .first()
            .is_some_and(|E| E.ticks_left == 0)
        {
            let E = WorldValue.to_be_ticked.remove(0);
            let B = WorldValue.get_block(E.pos);
            mchprs_redstone::tick(B, &mut WorldValue, E.pos);
            WorldValue
                .to_be_ticked
                .sort_by_key(|E| (E.ticks_left, E.tick_priority));
        }
        InitializationElapsed += 1;
    }
    let ReadInputs = |W: &TestWorld| -> Result<BTreeMap<String, bool>, String> {
        Fixture
            .Inputs
            .iter()
            .map(|P| Ok((P.Name.clone(), ReadBoolean(W, Translate(P.Position))?)))
            .collect()
    };
    let BaselineReadback = ReadInputs(&WorldValue)?;
    if BaselineReadback != InitialInputs {
        return Err("baseline-readback-mismatch".into());
    }
    let mut CompilerValue = Compiler::default();
    CompilerValue.compile(
        &WorldValue,
        (Translate(Minimum), Translate(Maximum)),
        CompilerOptions {
            io_only: false,
            optimize: false,
            ..Default::default()
        },
        Vec::new(),
        Arc::new(TaskMonitor::default()),
    );
    // Iterate the complete vector, checking actual state even for unchanged inputs.
    for P in &Fixture.Inputs {
        if ReadBoolean(&WorldValue, Translate(P.Position))? != AppliedInputs[&P.Name] {
            CompilerValue.on_use_block(Translate(P.Position));
        }
    }
    CompilerValue.flush(&mut WorldValue);
    let AppliedReadback = ReadInputs(&WorldValue)?;
    if AppliedReadback != AppliedInputs {
        return Err("applied-readback-mismatch".into());
    }
    let mut Samples = Vec::with_capacity(HorizonTicks + 1);
    for Tick in 0..=HorizonTicks {
        if Tick > 0 {
            CompilerValue.tick();
            CompilerValue.flush(&mut WorldValue);
        }
        let Outputs: BTreeMap<_, _> = Fixture
            .Outputs
            .iter()
            .map(|P| {
                Ok((
                    P.Name.clone(),
                    ReadBoolean(&WorldValue, Translate(P.Position))?,
                ))
            })
            .collect::<Result<_, String>>()?;
        let RootPower: BTreeMap<_, _> = Fixture
            .RouteInputs
            .iter()
            .map(|(Name, P)| {
                let Power = match WorldValue.get_block(Translate(*P)) {
                    Block::RedstoneWire(Wire) => Wire.power,
                    _ => 0,
                };
                (Name.clone(), Power)
            })
            .collect();
        Samples.push(json!({"Tick": Tick, "Outputs": Outputs, "Inputs": ReadInputs(&WorldValue)?, "RootPower": RootPower}));
    }
    Ok(
        json!({"Status": "observed", "SchemaVersion": "mchprs-observation-v1", "Backend": "mchprs-redpiler-fe217210", "FreshWorld": true, "FreshCompiler": true, "CompilerOptions": {"io_only": false, "optimize": false}, "InitialInputs": BaselineReadback, "AppliedInputs": AppliedReadback, "InitializationElapsedTicks": InitializationElapsed, "HorizonTicks": HorizonTicks, "Samples": Samples}),
    )
}

#[pyfunction]
pub fn ObserveMchprsFixture(RequestJson: &str) -> PyResult<String> {
    let RequestValue: Request = serde_json::from_str(RequestJson)
        .map_err(|E| pyo3::exceptions::PyValueError::new_err(E.to_string()))?;
    Ok(match Run(RequestValue) {
        Ok(Result) => Result,
        Err(Reason) => json!({"Status": if Reason.starts_with("unsupported") { "unsupported" } else { "backend-error" }, "Reason": Reason}),
    }.to_string())
}

#[cfg(test)]
mod Tests {
    use super::*;

    fn IdentityRequest(Initial: bool, Applied: bool) -> Request {
        serde_json::from_value(json!({
            "Fixture": {
                "Blocks": [
                    {"Position": [0,0,0], "State": {"Name": "minecraft:stone"}},
                    {"Position": [1,0,0], "State": {"Name": "minecraft:stone"}},
                    {"Position": [2,0,0], "State": {"Name": "minecraft:stone"}},
                    {"Position": [0,1,0], "State": {"Name": "minecraft:lever", "Properties": {"face": "floor", "facing": "north", "powered": "false"}}},
                    {"Position": [1,1,0], "State": {"Name": "minecraft:redstone_wire", "Properties": {"east": "side", "west": "side", "north": "none", "south": "none", "power": "0"}}},
                    {"Position": [2,1,0], "State": {"Name": "minecraft:redstone_lamp", "Properties": {"lit": "false"}}}
                ],
                "Inputs": [{"Name": "A", "Position": [0,1,0]}],
                "Outputs": [{"Name": "Y", "Position": [2,1,0]}],
                "RouteInputs": {"A": [1,1,0]}
            },
            "InitialInputs": {"A": Initial}, "AppliedInputs": {"A": Applied},
            "HorizonTicks": 5, "InitializationTicks": 100
        })).unwrap()
    }

    #[test]
    fn fresh_cases_observe_actual_control_root_and_lamp_transitions() {
        for (Initial, Applied) in [(true, false), (false, true), (true, false)] {
            let Result = Run(IdentityRequest(Initial, Applied)).unwrap();
            assert_eq!(Result["InitialInputs"], json!({"A": Initial}));
            assert_eq!(Result["AppliedInputs"], json!({"A": Applied}));
            let Samples = Result["Samples"].as_array().unwrap();
            assert_eq!(Samples.len(), 6);
            for (Tick, Sample) in Samples.iter().enumerate() {
                assert_eq!(Sample["Tick"], Tick);
                assert_eq!(Sample["Inputs"], json!({"A": Applied}));
            }
            assert_eq!(Samples[5]["Outputs"], json!({"Y": Applied}));
            assert_eq!(
                Samples[5]["RootPower"],
                json!({"A": if Applied {15} else {0}})
            );
        }
    }

    #[test]
    fn missing_vector_and_unsupported_probe_never_manufacture_samples() {
        let mut Request = IdentityRequest(false, true);
        Request.AppliedInputs.clear();
        assert!(Run(Request).unwrap_err().contains("input-vectors"));
        let mut Request = IdentityRequest(false, true);
        Request.Fixture.Outputs[0].Position = [2, 0, 0];
        assert!(Run(Request)
            .unwrap_err()
            .contains("unsupported-observation"));
    }
}
