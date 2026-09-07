"""Discover data-only cases, execute independently, and retain source-bound reports."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import platform
import subprocess
import sys
from time import monotonic

from PhysicalDesign.Redstone.FixtureValidation import CheckPhysicalFixture, ModelIdentity
from .Comparison import CompareCase
from .Expectations import ParseExpectations, ReadJson
from .Loaders import LoadFixture
from .Observation import ObserveCase


def CaptureSources(Descriptor: Path, Directory: Path) -> dict:
    """Retain bytes before schema/freshness validation can reject a fixture."""
    Paths = [Descriptor, Descriptor.with_name("Expectations.json")]
    Issues = []
    try:
        Metadata = ReadJson(Descriptor)
        SourcePath = Metadata.get("source", {}).get("path")
        if isinstance(SourcePath, str):
            Paths.append((Descriptor.parent / SourcePath).resolve())
        else:
            Issues.append({"Source": "fixture-source", "Reason": "source-path-unavailable-in-descriptor"})
    except Exception as Error:
        Issues.append({"Source": "fixture-source", "Reason": str(Error)})
    Records = []
    for Index, Source in enumerate(dict.fromkeys(Paths)):
        try:
            Content = Source.read_bytes()
            CopyPath = Directory / f"source-{Index:02d}-{Source.name}"
            CopyPath.write_bytes(Content)
            Records.append({"Path": str(Source.resolve()), "Sha256": sha256(Content).hexdigest(), "Copy": CopyPath.name})
        except OSError as Error:
            Issues.append({"Source": str(Source.resolve()), "Reason": str(Error)})
    Receipt = {"Sources": Records, "Unavailable": Issues}
    (Directory / "Sources.json").write_text(json.dumps(Receipt, sort_keys=True, indent=2) + "\n")
    return Receipt


def RunFixtures(FixtureRoot: Path, OutputRoot: Path) -> dict:
    """Attempt all discovered fixtures and cases; never reuse simulator state."""
    if OutputRoot.exists():
        raise ValueError("fresh-output-root-required")
    OutputRoot.mkdir(parents=True)
    Started = monotonic()
    Results = []
    for Index, Descriptor in enumerate(sorted(FixtureRoot.rglob("Fixture.json"))):
        Directory = OutputRoot / f"fixture-{Index:04d}"
        Directory.mkdir()
        Captured = CaptureSources(Descriptor, Directory)
        try:
            Fixture = LoadFixture(Descriptor)
            ExpectationsPath = Descriptor.with_name("Expectations.json")
            Cases = ParseExpectations(ExpectationsPath,
                                      {P["Name"] for P in Fixture.Document["Inputs"]},
                                      {P["Name"] for P in Fixture.Document["Outputs"]})
            SourceHashes = {**Fixture.SourceHashes, str(ExpectationsPath.resolve()): sha256(ExpectationsPath.read_bytes()).hexdigest()}
            CapturedHashes = {R["Path"]: R["Sha256"] for R in Captured["Sources"]}
            if CapturedHashes != SourceHashes:
                raise ValueError("source-changed-after-evidence-capture")
            (Directory / "Fixture.json").write_text(json.dumps(Fixture.Document, sort_keys=True, indent=2) + "\n")
            for CaseIndex, Case in enumerate(Cases):
                try:
                    # Neither executor receives the expectation object.
                    Prediction = CheckPhysicalFixture(Fixture.Document, ExpectedFixtureSha256=Fixture.Sha256, InputVector=Case.AppliedInputs)
                    Observation = ObserveCase(Fixture, Case.InitialInputs, Case.AppliedInputs, Case.SettlementTicks + Case.StabilityWindowTicks)
                    Result = CompareCase(Case, Prediction, Observation, FixtureSha256=Fixture.Sha256, ExpectedModel=ModelIdentity())
                    Evidence = {"Case": asdict(Case), "Prediction": Prediction, "Observation": Observation, "Result": Result,
                                "Sources": SourceHashes, "FixtureSha256": Fixture.Sha256}
                except Exception as Error:
                    Result = {"Status": "backend-error", "Reason": str(Error)}
                    Evidence = {"Case": asdict(Case), "Result": Result, "FixtureSha256": Fixture.Sha256}
                Artifact = Directory / f"case-{CaseIndex:04d}.json"
                Artifact.write_text(json.dumps(Evidence, sort_keys=True, indent=2) + "\n")
                Results.append({"Fixture": str(Descriptor), "Case": Case.Id, "Artifact": str(Artifact), **Result})
        except Exception as Error:
            Failure = {"Fixture": str(Descriptor), "Status": "configuration-error", "Reason": str(Error), "Sources": Captured}
            (Directory / "Failure.json").write_text(json.dumps(Failure, sort_keys=True, indent=2) + "\n")
            Results.append(Failure)
    Report = {"Status": "passed" if Results and all(R["Status"] == "passed" for R in Results) else "non-success",
              "RuntimeSeconds": monotonic() - Started, "Results": Results, "Model": ModelIdentity(),
              "Python": sys.executable, "Platform": platform.platform(), "Command": sys.argv,
              "GitHead": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
              "Limitations": ["Conditional route transfer only; no general device truth or model timing prediction.", "No Fabric, scale, or full-router acceptance."]}
    (OutputRoot / "Results.json").write_text(json.dumps(Report, sort_keys=True, indent=2) + "\n")
    (OutputRoot / "RawDump.txt").write_text(json.dumps(Report, sort_keys=True, indent=2) + "\n")
    (OutputRoot / "Summary.txt").write_text(f"RESULT: {Report['Status']}\nTIME: {Report['RuntimeSeconds']:.3f}s\nOUTPUT: {OutputRoot}\n" + "\n".join(f"{R.get('Case', R['Fixture'])}: {R['Status']}" for R in Results) + "\n")
    return Report


def Main() -> int:
    Parser = argparse.ArgumentParser(description=__doc__)
    Parser.add_argument("--fixtures", type=Path, default=Path("Tests/Fixtures/PhysicalRulesBatch1"))
    Parser.add_argument("--output", type=Path, default=Path("Output/PhysicalRulesBatch1") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    Args = Parser.parse_args()
    Result = RunFixtures(Args.fixtures, Args.output)
    print((Args.output / "Summary.txt").read_text())
    return 0 if Result["Status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(Main())
