"""Real bounded strategy-identity coverage without a live Fabric server."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys

import pytest

from App.BenchmarkArchive import (
    BenchmarkArchiveContext,
    BuildBenchmarkArchiveIdentity,
    PublishBenchmarkArchive,
)
from Compilation.Ir.Models import Gate, GateKind, ModuleIR, NetlistIR
from PhysicalDesign.Orchestration.Runner import PlaceAndRoutePcb
from PhysicalDesign.Rendering.SchemWriter import BuildLitematicBlockMap, WriteLitematic
from PhysicalDesign.Policy import RoutingStrategy
from Tools.Routing.RunRouterAcceptance import (
    AcceptanceCase,
    AcceptanceCommandResult,
    AcceptanceConfiguration,
    BuildCompilerCommand,
    BuildPolicyProvenanceRecord,
    BuildRunArtifacts,
    BuildSourceProvenance,
    EvaluateRun,
)


def _BuildFanoutNetlist() -> NetlistIR:
    """Create the smallest public fanout that reaches the Joint router."""
    Module = ModuleIR(
        Name="TelemetryStrategyFanout",
        Inputs=["A"],
        Outputs=["T", "Z"],
        Gates=[
            Gate("InputA", GateKind.INPUT, ["A"]),
            Gate("Nand0", GateKind.NAND, ["T"], ["A", "A"]),
            Gate("Nand1", GateKind.NAND, ["Z"], ["T", "T"]),
            Gate("OutputT", GateKind.OUTPUT, [], ["T"]),
            Gate("OutputZ", GateKind.OUTPUT, [], ["Z"]),
        ],
    )
    return NetlistIR(Top=Module.Name, Modules={Module.Name: Module})


def _CanonicalJsonSha256(Value: object) -> str:
    return sha256(json.dumps(
        Value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _WriteCommandInput(Root: Path) -> Path:
    """Create the real declared source used only to construct a command."""
    SourcePath = Root / "Assets" / "Examples" / "TelemetryStrategyFanout.sv"
    SourcePath.parent.mkdir(parents=True)
    SourcePath.write_text(
        """module TelemetryStrategyFanout (
    input logic A,
    output logic T,
    output logic Z
);
    assign T = ~(A & A);
    assign Z = ~(T & T);
endmodule
""",
        encoding="utf-8",
    )
    return SourcePath


def _Case(SourcePath: Path) -> AcceptanceCase:
    return AcceptanceCase(
        Name="TelemetryStrategyFanout",
        ExamplePath=SourcePath,
        TopModule="TelemetryStrategyFanout",
        RequiredRuns=1,
        TruthTableRows=1,
        RuntimeCeilingSeconds=30.0,
        PublicationReserveSeconds=1.0,
    )


def _Configuration(
    Root: Path,
    RequestedStrategy: str,
) -> AcceptanceConfiguration:
    Policy = BuildPolicyProvenanceRecord(RequestedStrategy)
    return AcceptanceConfiguration(
        RepositoryRoot=Path(__file__).parents[2],
        OutputRoot=Root,
        DateLabel="2026-09-06",
        PythonExecutable=Path(sys.executable),
        RequestedRoutingStrategy=RequestedStrategy,
        ExpectedPolicyVersion=str(Policy["PolicyVersion"]),
    )


def _WriteRealFanoutIdentityArtifact(
    Artifacts: dict[str, Path],
    RequestedStrategy: str,
) -> tuple[dict[str, object], dict[str, object]]:
    """Persist only the public physical facts produced by the real fanout."""
    Result = PlaceAndRoutePcb(
        _BuildFanoutNetlist(),
        Strategy=RequestedStrategy,
    )
    Artifacts["RunDirectory"].mkdir(parents=True, exist_ok=True)
    WriteLitematic(
        Result.Routed,
        Artifacts["Schematic"],
        Build=BuildLitematicBlockMap(Result.Routed),
    )
    ActualPolicy = Result.Policy.ToDictionary()
    ActualStrategy = {
        "Requested": Result.RequestedStrategy,
        "Used": Result.UsedStrategy,
        "FallbackUsed": Result.FallbackUsed,
        "FallbackReason": Result.FallbackReason,
    }
    Artifacts["PhysicalDesign"].write_text(
        json.dumps({
            "Strategy": ActualStrategy,
            "Policy": ActualPolicy,
        }, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ActualPolicy, ActualStrategy


def _ArchiveContext(
    Root: Path,
    RepositoryRoot: Path,
) -> BenchmarkArchiveContext:
    Identity = BuildBenchmarkArchiveIdentity(RepositoryRoot)
    return BenchmarkArchiveContext(
        Identity=Identity,
        ArchiveDirectory=Root / "Archive",
        SourceDirectory=Root / "Session",
        Command=("test", "real-fanout"),
        WorkingDirectory=RepositoryRoot,
        MatrixMode="default",
        RoutingThreads=None,
        BaselineMode=None,
        StartedAtUtc="2026-09-06T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    "RequestedStrategy",
    (
        RoutingStrategy.Default.value,
        RoutingStrategy.RoutingAwarePlacementAccess.value,
    ),
)
def test_real_fanout_identity_survives_evaluation_and_failed_archive(
    tmp_path: Path,
    RequestedStrategy: str,
) -> None:
    """No-Fabric failure must retain, rather than erase, real identity gates."""
    ExpectedUsedStrategy = RequestedStrategy
    CommandRoot = tmp_path / "CommandWorkspace"
    Case = _Case(_WriteCommandInput(CommandRoot))
    Configuration = _Configuration(CommandRoot, RequestedStrategy)
    Artifacts = BuildRunArtifacts(
        tmp_path / "Session" / RequestedStrategy / "Run",
        "TelemetryStrategyFanoutRun1",
    )
    ActualPolicy, ActualStrategy = _WriteRealFanoutIdentityArtifact(
        Artifacts,
        RequestedStrategy,
    )
    ExpectedIdentity = BuildPolicyProvenanceRecord(RequestedStrategy)
    Command = BuildCompilerCommand(
        Configuration,
        Case,
        "TelemetryStrategyFanoutRun1",
        Artifacts,
    )

    assert Command[Command.index("--routing-strategy") + 1] == RequestedStrategy
    assert Path(Command[1]) == Path(__file__).parents[2] / "Main.py"
    assert Path(Command[Command.index("--input") + 1]) == Case.ExamplePath
    assert json.loads(json.dumps(ActualPolicy)) == ExpectedIdentity["Snapshot"]
    assert _CanonicalJsonSha256(ActualPolicy) == ExpectedIdentity["Sha256"]
    assert ExpectedIdentity["UsedRoutingStrategy"] == ExpectedUsedStrategy
    assert ActualStrategy == {
        "Requested": RequestedStrategy,
        "Used": ExpectedUsedStrategy,
        "FallbackUsed": False,
        "FallbackReason": None,
    }

    Evaluation, _Evidence = EvaluateRun(
        Case=Case,
        Process=AcceptanceCommandResult(1, "", "", 0.0),
        Artifacts=Artifacts,
        ExpectedSeed=0,
        ExpectedPolicyVersion=str(ExpectedIdentity["PolicyVersion"]),
        ExpectedRoutingStrategy=RequestedStrategy,
        ExpectedPolicyProvenance=ExpectedIdentity,
        ExpectedCommand=Command,
    )

    assert Evaluation["Accepted"] is False
    assert Evaluation["Observed"]["FabricValidationStatus"] != "passed"
    Checks = Evaluation["Observed"]["RoutingIdentityChecks"]
    assert Checks["ConfiguredCommandMatches"] is True
    assert Checks["ConfiguredSourcePolicyMatches"] is True
    assert Checks["ActualArtifactPresent"] is True
    assert Checks["ActualRequestedStrategyMatches"] is True
    assert Checks["ActualUsedStrategyMatches"] is True
    assert Checks["ActualFallbackDisabled"] is True
    assert Checks["ActualPolicySnapshotMatches"] is True
    assert Checks["ActualPolicyIdentityMatches"] is True

    Session = tmp_path / "Session"
    (Session / "Summary.txt").write_text(
        "RESULT: FAILURE\nOUTPUT: Fabric validation not-run\n",
        encoding="utf-8",
    )
    (Session / "RawDump.txt").write_text("real bounded fanout\n", encoding="utf-8")
    RepositoryRoot = Path(__file__).parents[2]
    ArchiveContext = _ArchiveContext(tmp_path, RepositoryRoot)
    SourceProvenance = BuildSourceProvenance(
        _Configuration(RepositoryRoot, RequestedStrategy),
        {
            "Revision": ArchiveContext.Identity.Source.Head,
            "Dirty": ArchiveContext.Identity.Source.Dirty,
            "Branch": ArchiveContext.Identity.Source.Branch,
        },
    )
    assert SourceProvenance["Git"]["Revision"] == (
        ArchiveContext.Identity.Source.Head
    )
    assert SourceProvenance["NativeExtension"]["Loaded"] is True
    assert SourceProvenance["Policy"] == ExpectedIdentity
    Manifest = {
        "Accepted": False,
        "Status": "FAILED",
        "SourceProvenanceStable": True,
        "RoutingIdentity": {
            "ConfiguredRequestedStrategy": RequestedStrategy,
            "ExpectedUsedStrategy": ExpectedUsedStrategy,
            "PolicyIdentity": ExpectedIdentity,
        },
        "SourceProvenance": SourceProvenance,
        "Runs": [{
            "RunName": "TelemetryStrategyFanoutRun1",
            "Accepted": False,
            "Evaluation": Evaluation,
        }],
    }
    Published = PublishBenchmarkArchive(
        ArchiveContext,
        Manifest,
        CompletedAtUtc="2026-09-06T00:00:01+00:00",
        WallSeconds=0.0,
        ExitCode=1,
        ExitClassification="infrastructure-failure",
    )
    ArchivedResult = json.loads(
        (Published / "BenchmarkResult.json").read_text(encoding="utf-8")
    )
    ArchiveManifest = json.loads(
        (Published / "ArchiveManifest.json").read_text(encoding="utf-8")
    )

    assert ArchivedResult["Accepted"] is False
    assert ArchiveManifest["Publication"]["Status"] == "SEALED"
    assert ArchiveManifest["Benchmark"]["Accepted"] is False
    assert ArchiveManifest["Source"]["Stable"] is True
    assert ArchiveManifest["Source"]["Start"]["Head"] == (
        ArchiveContext.Identity.Source.Head
    )
    assert ArchiveManifest["Source"]["End"]["Head"] == (
        ArchiveContext.Identity.Source.Head
    )
    assert ArchiveManifest["Runtime"]["RoutingIdentity"] == {
        "ConfiguredRequestedStrategy": RequestedStrategy,
        "ExpectedUsedStrategy": ExpectedUsedStrategy,
        "PolicyIdentity": ExpectedIdentity,
    }
    ArchivedChecks = ArchivedResult["Runs"][0]["Evaluation"]["Observed"][
        "RoutingIdentityChecks"
    ]
    assert all(
        ArchivedChecks[Name] is True
        for Name in (
            "ConfiguredCommandMatches",
            "ConfiguredSourcePolicyMatches",
            "ActualArtifactPresent",
            "ActualRequestedStrategyMatches",
            "ActualUsedStrategyMatches",
            "ActualFallbackDisabled",
            "ActualPolicySnapshotMatches",
            "ActualPolicyIdentityMatches",
        )
    )


def test_real_fanout_requested_strategy_mutation_fails_its_identity_gate(
    tmp_path: Path,
) -> None:
    """One changed identity link is observable even while both runs reject."""
    RequestedStrategy = RoutingStrategy.Default.value
    ExpectedUsedStrategy = RequestedStrategy
    CommandRoot = tmp_path / "CommandWorkspace"
    Case = _Case(_WriteCommandInput(CommandRoot))
    Configuration = _Configuration(CommandRoot, RequestedStrategy)
    Artifacts = BuildRunArtifacts(tmp_path / "Run", "TelemetryStrategyFanoutRun1")
    _ActualPolicy, ActualStrategy = _WriteRealFanoutIdentityArtifact(
        Artifacts,
        RequestedStrategy,
    )
    ExpectedIdentity = BuildPolicyProvenanceRecord(RequestedStrategy)
    Command = BuildCompilerCommand(
        Configuration,
        Case,
        "TelemetryStrategyFanoutRun1",
        Artifacts,
    )
    Physical = json.loads(Artifacts["PhysicalDesign"].read_text(encoding="utf-8"))
    Physical["Strategy"] = dict(Physical["Strategy"])
    assert ActualStrategy["Used"] == ExpectedUsedStrategy
    Physical["Strategy"]["Used"] = (
        RoutingStrategy.RoutingAwarePlacementAccess.value
    )
    Artifacts["PhysicalDesign"].write_text(
        json.dumps(Physical, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    Evaluation, _Evidence = EvaluateRun(
        Case=Case,
        Process=AcceptanceCommandResult(1, "", "", 0.0),
        Artifacts=Artifacts,
        ExpectedSeed=0,
        ExpectedPolicyVersion=str(ExpectedIdentity["PolicyVersion"]),
        ExpectedRoutingStrategy=RequestedStrategy,
        ExpectedPolicyProvenance=ExpectedIdentity,
        ExpectedCommand=Command,
    )

    assert Evaluation["Accepted"] is False
    Checks = Evaluation["Observed"]["RoutingIdentityChecks"]
    assert Checks["ActualUsedStrategyMatches"] is False
    for Name in (
        "ConfiguredCommandMatches",
        "ConfiguredSourcePolicyMatches",
        "ActualArtifactPresent",
        "ActualRequestedStrategyMatches",
        "ActualFallbackDisabled",
        "ActualPolicySnapshotMatches",
        "ActualPolicyIdentityMatches",
    ):
        assert Checks[Name] is True
