"""Independent, pinned source-to-optimized-NAND SAT differential tests.

Set RC_REQUIRE_SOURCE_ORACLE=1 for the acceptance gate. Missing tools then fail
instead of skipping. Evidence is always retained under a fresh run directory.
"""

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

import pytest

from Compilation.Ir.Models import Gate, GateKind
from Compilation.Synthesis.LogicOptimization import OptimizeLogic
from Compilation.Synthesis.NandTransform import ToNandOnly
from Compilation.Synthesis.Validation import ValidateNandOnlyDesign
from Formats.SystemVerilog.Sv import ParseSvToNetlist
from Tests.Compiler.Synthesis.SourceCases import (
    ExampleInterfaces, GenerateSource, NamespaceCases, ObservableOutputCases,
    QualifierCases, SourceCase, SourceCases,
)
from Tests.Compiler.Synthesis.SourceOracle import (
    ClassifyProof, OracleOutcome, PinnedPackage, PinnedYosysVersion,
    ProcessResult, ProveSourceToNand, RunBounded,
)


@pytest.fixture(scope="session")
def OracleTool():
    Requested = os.environ.get("RC_YOSYS", "yowasp-yosys")
    Executable = shutil.which(Requested)
    if Executable is None:
        Message = f"Independent source oracle requires {PinnedPackage}; install Tests/Compiler/Synthesis/oracle-requirements.txt or set RC_YOSYS"
        if os.environ.get("RC_REQUIRE_SOURCE_ORACLE") == "1":
            pytest.fail(Message)
        pytest.skip(Message)
    Root = Path(os.environ.get("RC_SOURCE_ORACLE_EVIDENCE", "Output/SourceOracle")) / uuid4().hex
    Root.mkdir(parents=True, exist_ok=False)
    Process = RunBounded([Executable, "-V"], Root, "version", Timeout=120.0)
    Version = (Root / "version.stdout.log").read_text().strip()
    assert not Process.TimedOut, f"Yosys version probe timed out; evidence: {Root}"
    assert Process.ReturnCode == 0, f"Yosys version probe failed: {Process}; evidence: {Root}"
    assert Version.startswith(PinnedYosysVersion), f"Use {PinnedPackage}; actual version {Version}; evidence: {Root}"
    return Executable, Version, Root


def CompileCase(Case, Directory):
    Directory.mkdir(parents=True, exist_ok=False)
    SourcePath = Directory / "input.sv"
    SourcePath.write_text(Case.Source, encoding="utf-8")
    Parsed = ParseSvToNetlist(InputPath=SourcePath, TopModule=Case.Top)
    assert Parsed.Top == Case.Top
    Module = Parsed.Modules[Parsed.Top]
    assert tuple(Module.Inputs) == Case.Inputs
    assert tuple(Module.Outputs) == Case.Outputs
    Optimized = OptimizeLogic(Parsed)
    Nand = ToNandOnly(Optimized)
    ValidateNandOnlyDesign(Nand)
    for Stage in (Optimized, Nand):
        assert Stage.Top == Case.Top
        assert tuple(Stage.Modules[Stage.Top].Inputs) == Case.Inputs
        assert tuple(Stage.Modules[Stage.Top].Outputs) == Case.Outputs
    return Module, Optimized.Modules[Optimized.Top], Nand.Modules[Nand.Top]


@pytest.mark.parametrize("Case", (*SourceCases, *NamespaceCases, *ObservableOutputCases, *QualifierCases), ids=lambda Case: Case.Name)
def test_source_matches_optimized_nand_with_independent_sat(Case, OracleTool):
    Executable, Version, Root = OracleTool
    Parsed, Optimized, Nand = CompileCase(Case, Root / f"{Case.Name}-compile")
    Result = ProveSourceToNand(
        Source=Case.Source, Parsed=Parsed, Optimized=Optimized, Nand=Nand,
        Seed=Case.Seed, Directory=Root / Case.Name, Executable=Executable,
        ToolVersion=Version,
    )
    assert Result.Outcome == OracleOutcome.PROVED_EQUIVALENT, Result
    Manifest = json.loads((Result.EvidenceDirectory / "result.json").read_text())
    assert set(Manifest["hashes"]) == {"source.sv", "parsed.ir.json", "optimized.ir.json", "nand.ir.json"}
    assert Manifest["seed"] == Case.Seed
    assert Manifest["tool_version"] == Version
    for Filename, ExpectedHash in Manifest["hashes"].items():
        assert sha256((Result.EvidenceDirectory / Filename).read_bytes()).hexdigest() == ExpectedHash


@pytest.mark.parametrize("Name", sorted(ExampleInterfaces))
def test_shipped_example_matches_independent_sat(Name, OracleTool):
    Executable, Version, Root = OracleTool
    SourcePath = Path("Assets/Examples") / f"{Name}.sv"
    Inputs, Outputs = ExampleInterfaces[Name]
    Case = SourceCase(Name, SourcePath.read_text(encoding="utf-8"), Name, Inputs, Outputs)
    Parsed, Optimized, Nand = CompileCase(Case, Root / f"example-{Name}-compile")
    Result = ProveSourceToNand(
        Source=Case.Source, Parsed=Parsed, Optimized=Optimized, Nand=Nand,
        Seed=None, Directory=Root / f"example-{Name}", Executable=Executable,
        ToolVersion=Version,
    )
    assert Result.Outcome == OracleOutcome.PROVED_EQUIVALENT, Result


def test_all_shipped_sources_are_in_oracle_matrix():
    assert set(ExampleInterfaces) == {PathValue.stem for PathValue in Path("Assets/Examples").glob("*.sv")}


def test_oracle_detects_corrupted_lowered_output(OracleTool):
    Executable, Version, Root = OracleTool
    Case = SourceCases[0]
    Parsed, Optimized, Nand = CompileCase(Case, Root / "negative-control-compile")
    Corrupted = deepcopy(Nand)
    Output = next(GateValue for GateValue in Corrupted.Gates if GateValue.Kind == GateKind.OUTPUT)
    Original = Output.Inputs[0]
    Corrupted.Gates.insert(Corrupted.Gates.index(Output), Gate(
        Name="NegativeControlInversion", Kind=GateKind.NAND,
        Inputs=[Original, Original], Outputs=["NegativeControlNet"],
    ))
    Output.Inputs[0] = "NegativeControlNet"
    Result = ProveSourceToNand(
        Source=Case.Source, Parsed=Parsed, Optimized=Optimized, Nand=Corrupted,
        Seed=Case.Seed, Directory=Root / "negative-control", Executable=Executable,
        ToolVersion=Version,
    )
    assert Result.Outcome == OracleOutcome.COUNTEREXAMPLE, Result
    assert set(Result.InputAssignment) == set(Case.Inputs)
    assert Result.SourceOutputs != Result.NandOutputs
    Manifest = json.loads((Result.EvidenceDirectory / "result.json").read_text())
    assert Manifest["result"]["InputAssignment"] == Result.InputAssignment
    assert (Result.EvidenceDirectory / "counterexample.json").is_file()


def test_generator_is_reproducible_and_changes_with_seed():
    assert GenerateSource(31) == GenerateSource(31)
    assert GenerateSource(31).Source != GenerateSource(32).Source
    assert len({Case.Source for Case in SourceCases}) == len(SourceCases)


@pytest.mark.parametrize(("ReturnCode", "TimedOut", "Log", "Expected"), [
    (0, False, "SAT proof finished - no model found: SUCCESS!", OracleOutcome.PROVED_EQUIVALENT),
    (1, False, "SAT proof finished - no model found: SUCCESS!", OracleOutcome.TOOL_FAILURE),
    (0, True, "SAT proof finished - no model found: SUCCESS!", OracleOutcome.TIMEOUT),
    (0, False, "SAT solving timed out", OracleOutcome.TIMEOUT),
    (0, False, "", OracleOutcome.UNKNOWN),
    (0, False, "SAT proof finished - model found: FAIL!", OracleOutcome.UNKNOWN),
    (0, False, "SAT proof finished - no model found: SUCCESS! SAT proof finished - model found: FAIL!", OracleOutcome.UNKNOWN),
])
def test_oracle_never_treats_inconclusive_result_as_success(tmp_path, ReturnCode, TimedOut, Log, Expected):
    Result = ClassifyProof(ProcessResult(ReturnCode, TimedOut, 0.0), Log, tmp_path, ["a"], ["y"])
    assert Result.Outcome == Expected


def test_missing_executable_is_tool_failure(tmp_path):
    Process = RunBounded([str(tmp_path / "missing-yosys")], tmp_path, "missing", 0.1)
    Result = ClassifyProof(Process, "", tmp_path, ["a"], ["y"])
    assert Result.Outcome == OracleOutcome.TOOL_FAILURE


def test_real_subprocess_timeout_is_bounded_and_not_a_proof(tmp_path):
    Process = RunBounded([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path, "timeout", 0.05)
    Result = ClassifyProof(Process, "", tmp_path, ["a"], ["y"])
    assert Result.Outcome == OracleOutcome.TIMEOUT
    assert Process.Seconds < 5
    assert (tmp_path / "timeout.stdout.log").is_file()


def test_unpinned_tool_is_unsupported_not_a_proof(tmp_path):
    Case = SourceCases[2]
    Parsed, Optimized, Nand = CompileCase(Case, tmp_path / "compile")
    Result = ProveSourceToNand(
        Source=Case.Source, Parsed=Parsed, Optimized=Optimized, Nand=Nand,
        Seed=None, Directory=tmp_path / "proof", Executable="unused",
        ToolVersion="Yosys unpinned",
    )
    assert Result.Outcome == OracleOutcome.UNSUPPORTED
    assert json.loads((Result.EvidenceDirectory / "result.json").read_text())["result"]["Outcome"] == "unsupported"


def test_success_log_with_conflicting_counterexample_is_unknown(tmp_path):
    (tmp_path / "counterexample.json").write_text("{}")
    Result = ClassifyProof(
        ProcessResult(0, False, 0.0),
        "SAT proof finished - no model found: SUCCESS!", tmp_path, ["a"], ["y"],
    )
    assert Result.Outcome == OracleOutcome.UNKNOWN


@pytest.mark.parametrize("Witness", [
    "not JSON",
    json.dumps({"signal": []}),
    json.dumps({"signal": [
        {"name": "in_0", "wave": "0"},
        {"name": "gold_0", "wave": "1"},
        {"name": "nand_0", "wave": "1"},
        {"name": "mismatch", "wave": "1"},
    ]}),
])
def test_missing_or_non_distinguishing_counterexample_is_unknown(tmp_path, Witness):
    (tmp_path / "counterexample.json").write_text(Witness)
    Result = ClassifyProof(
        ProcessResult(0, False, 0.0),
        "SAT proof finished - model found: FAIL!", tmp_path, ["a"], ["y"],
    )
    assert Result.Outcome == OracleOutcome.UNKNOWN
