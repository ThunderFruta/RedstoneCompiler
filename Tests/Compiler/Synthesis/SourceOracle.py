"""Test-only independent Yosys/SAT oracle for source-to-NAND equivalence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import subprocess
from time import monotonic

from Compilation.Ir.Models import GateKind, ModuleIR


PinnedYosysVersion = "Yosys 0.69 (git sha1 9f75ca1f9,"
PinnedPackage = "yowasp-yosys==0.69.0.0.post1233"


class OracleOutcome(str, Enum):
    PROVED_EQUIVALENT = "proved-equivalent"
    COUNTEREXAMPLE = "counterexample"
    UNSUPPORTED = "unsupported"
    TIMEOUT = "timeout"
    TOOL_FAILURE = "tool-failure"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProcessResult:
    ReturnCode: int | None
    TimedOut: bool
    Seconds: float
    Error: str | None = None


@dataclass(frozen=True)
class OracleResult:
    Outcome: OracleOutcome
    EvidenceDirectory: Path
    InputAssignment: dict[str, bool] | None = None
    SourceOutputs: dict[str, bool] | None = None
    NandOutputs: dict[str, bool] | None = None
    Detail: str = ""


def RunBounded(
    Command: list[str], Directory: Path, Prefix: str, Timeout: float,
) -> ProcessResult:
    """Bound tool wall time and retain logs even for launch/timeout failures."""
    Start = monotonic()
    with (Directory / f"{Prefix}.stdout.log").open("w") as Stdout, (
        Directory / f"{Prefix}.stderr.log"
    ).open("w") as Stderr:
        try:
            Process = subprocess.Popen(
                Command, cwd=Directory, stdout=Stdout, stderr=Stderr,
                start_new_session=os.name == "posix",
            )
        except OSError as Error:
            return ProcessResult(None, False, monotonic() - Start, str(Error))
        try:
            ReturnCode = Process.wait(timeout=Timeout)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(Process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                Process.kill()
            Process.wait()
            return ProcessResult(Process.returncode, True, monotonic() - Start)
    return ProcessResult(ReturnCode, False, monotonic() - Start)


def SerializeModule(Module: ModuleIR) -> str:
    """Use a path-independent IR identity, while retaining all logic fields."""
    Payload = asdict(Module)
    Payload.pop("SourcePath")
    return json.dumps(Payload, sort_keys=True, indent=2) + "\n"


def EscapeIdentifier(Name: str) -> str:
    return f"\\{Name} "


def EmitNandModule(Module: ModuleIR) -> str:
    """Render each NAND independently; never call the compiler's evaluator."""
    Signals = sorted({
        *Module.Inputs,
        *(Name for Gate in Module.Gates for Name in Gate.Inputs + Gate.Outputs),
    })
    Names = {Name: f"n{Index}" for Index, Name in enumerate(Signals)}
    Lines = ["module OracleLowered (", ",\n".join([
        *(f"  input i{Index}" for Index in range(len(Module.Inputs))),
        *(f"  output o{Index}" for Index in range(len(Module.Outputs))),
    ]), ");", *(f"wire {Name};" for Name in Names.values())]
    Lines.extend(
        f"assign {Names[Name]} = i{Index};"
        for Index, Name in enumerate(Module.Inputs)
    )
    OutputRoots = {}
    for Gate in Module.Gates:
        if len(Gate.Outputs) != 1:
            raise ValueError("Oracle supports exactly one output per gate")
        if Gate.Kind == GateKind.INPUT:
            if Gate.Inputs or Gate.Output not in Module.Inputs:
                raise ValueError("Malformed input boundary")
        elif Gate.Kind == GateKind.NAND and len(Gate.Inputs) == 2:
            Left, Right = (Names[Name] for Name in Gate.Inputs)
            Lines.append(f"assign {Names[Gate.Output]} = ~({Left} & {Right});")
        elif Gate.Kind == GateKind.OUTPUT and len(Gate.Inputs) == 1:
            Lines.append(f"assign {Names[Gate.Output]} = {Names[Gate.Inputs[0]]};")
            OutputRoots[Gate.Output] = Names[Gate.Output]
        else:
            raise ValueError(f"Unsupported NAND oracle gate {Gate.Kind}")
    for Index, Name in enumerate(Module.Outputs):
        Lines.append(f"assign o{Index} = {OutputRoots[Name + '$Output']};")
    return "\n".join([*Lines, "endmodule", ""])


def EmitMiter(Module: ModuleIR) -> str:
    Ports = [
        *(f"input in_{Index}" for Index in range(len(Module.Inputs))),
        *(f"output gold_{Index}" for Index in range(len(Module.Outputs))),
        *(f"output nand_{Index}" for Index in range(len(Module.Outputs))),
        "output mismatch",
    ]
    GoldConnections = [
        *(f".{EscapeIdentifier(Name)}(in_{Index})" for Index, Name in enumerate(Module.Inputs)),
        *(f".{EscapeIdentifier(Name)}(gold_{Index})" for Index, Name in enumerate(Module.Outputs)),
    ]
    NandConnections = [
        *(f".i{Index}(in_{Index})" for Index in range(len(Module.Inputs))),
        *(f".o{Index}(nand_{Index})" for Index in range(len(Module.Outputs))),
    ]
    Differences = " | ".join(
        f"(gold_{Index} ^ nand_{Index})" for Index in range(len(Module.Outputs))
    )
    return "\n".join([
        f"module OracleMiter ({', '.join(Ports)});",
        f"OracleGold source_instance ({', '.join(GoldConnections)});",
        f"OracleLowered nand_instance ({', '.join(NandConnections)});",
        f"assign mismatch = {Differences};", "endmodule", "",
    ])


def ReadCounterexample(
    PathValue: Path, Inputs: list[str], Outputs: list[str],
) -> tuple[dict[str, bool], dict[str, bool], dict[str, bool]]:
    Wave = json.loads(PathValue.read_text(encoding="utf-8"))
    Signals = {}
    for Entry in Wave["signal"]:
        Name = Entry["name"].removeprefix("\\")
        Value = Entry.get("data", [""])[0]
        if Value in ("0", "1"):
            Signals[Name] = Value == "1"
        elif Entry.get("wave", "")[0:1] in ("0", "1"):
            Signals[Name] = Entry["wave"][0] == "1"
    Assignment = {Name: Signals[f"in_{Index}"] for Index, Name in enumerate(Inputs)}
    SourceOutputs = {Name: Signals[f"gold_{Index}"] for Index, Name in enumerate(Outputs)}
    NandOutputs = {Name: Signals[f"nand_{Index}"] for Index, Name in enumerate(Outputs)}
    if Signals.get("mismatch") is not True or SourceOutputs == NandOutputs:
        raise ValueError("SAT model does not witness unequal outputs")
    return Assignment, SourceOutputs, NandOutputs


def ClassifyProof(
    Process: ProcessResult, Log: str, Directory: Path,
    Inputs: list[str], Outputs: list[str],
) -> OracleResult:
    if Process.TimedOut or "SAT solving timed out" in Log:
        return OracleResult(OracleOutcome.TIMEOUT, Directory)
    if Process.Error or Process.ReturnCode != 0:
        return OracleResult(OracleOutcome.TOOL_FAILURE, Directory, Detail=Process.Error or Log[-2000:])
    Passed = "SAT proof finished - no model found: SUCCESS!" in Log
    Failed = "SAT proof finished - model found: FAIL!" in Log
    if Passed and not Failed and not (Directory / "counterexample.json").exists():
        return OracleResult(OracleOutcome.PROVED_EQUIVALENT, Directory)
    if Failed and not Passed:
        try:
            Assignment, SourceOutputs, NandOutputs = ReadCounterexample(
                Directory / "counterexample.json", Inputs, Outputs,
            )
        except (OSError, ValueError, KeyError, TypeError, IndexError) as Error:
            return OracleResult(OracleOutcome.UNKNOWN, Directory, Detail=f"Invalid SAT model: {Error}")
        return OracleResult(
            OracleOutcome.COUNTEREXAMPLE, Directory, Assignment,
            SourceOutputs, NandOutputs,
        )
    return OracleResult(OracleOutcome.UNKNOWN, Directory, Detail="No unambiguous SAT result")


def ProveSourceToNand(
    *, Source: str, Parsed: ModuleIR, Optimized: ModuleIR, Nand: ModuleIR,
    Seed: int | None, Directory: Path, Executable: str, ToolVersion: str,
    Timeout: float = 30.0,
) -> OracleResult:
    """Compare original HDL against the actual optimized/lowered production IR."""
    Directory.mkdir(parents=True, exist_ok=False)
    (Directory / "source.sv").write_text(Source, encoding="utf-8")
    Hashes = {"source.sv": sha256(Source.encode()).hexdigest()}
    for Label, Module in (("parsed", Parsed), ("optimized", Optimized), ("nand", Nand)):
        Contents = SerializeModule(Module)
        Filename = f"{Label}.ir.json"
        (Directory / Filename).write_text(Contents, encoding="utf-8")
        Hashes[Filename] = sha256(Contents.encode()).hexdigest()
    Script = "\n".join([
        "read_verilog -sv source.sv",
        f"hierarchy -check -top {Parsed.Name}",
        f"rename {Parsed.Name} OracleGold",
        "read_verilog -sv lowered.sv miter.sv",
        "hierarchy -check -top OracleMiter", "proc", "flatten", "opt",
        "check -assert",
        "sat -prove mismatch 0 -show-inputs -show-outputs -timeout 10 -dump_json counterexample.json",
        "",
    ])
    Command = [Executable, "-T", "-s", "proof.ys"]
    Process = None
    if not ToolVersion.startswith(PinnedYosysVersion):
        Result = OracleResult(OracleOutcome.UNSUPPORTED, Directory, Detail=f"Expected {PinnedPackage}; got {ToolVersion}")
    else:
        try:
            (Directory / "lowered.sv").write_text(EmitNandModule(Nand), encoding="utf-8")
            (Directory / "miter.sv").write_text(EmitMiter(Nand), encoding="utf-8")
            (Directory / "proof.ys").write_text(Script, encoding="utf-8")
        except (ValueError, KeyError) as Error:
            Result = OracleResult(OracleOutcome.UNSUPPORTED, Directory, Detail=str(Error))
        else:
            Process = RunBounded(Command, Directory, "yosys", Timeout)
            Log = (Directory / "yosys.stdout.log").read_text() + (Directory / "yosys.stderr.log").read_text()
            Result = ClassifyProof(Process, Log, Directory, Nand.Inputs, Nand.Outputs)
    Manifest = {
        "schema": 1, "seed": Seed, "top": Parsed.Name,
        "tool_package": PinnedPackage, "tool_version": ToolVersion,
        "command": Command, "timeout_seconds": Timeout, "hashes": Hashes,
        "process": asdict(Process) if Process else None,
        "result": asdict(Result),
    }
    (Directory / "result.json").write_text(json.dumps(Manifest, default=str, sort_keys=True, indent=2) + "\n")
    return Result
