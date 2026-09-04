"""MCHPRS Redpiler physical-validation boundary."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from time import monotonic
from typing import Callable

from Validation.Physical import BuildValidationAssignments, ExhaustiveInputLimit, PackAssignment, PhysicalFixtureArtifact, PhysicalValidationProgress, PhysicalValidationResult


@dataclass(frozen=True)
class MchprsConfiguration:
    """Runtime policy for embedded Redpiler validation."""

    ExhaustiveInputLimit: int = ExhaustiveInputLimit
    MaximumSettleTicks: int = 100


class MchprsValidator:
    """Validate a physical fixture with embedded MCHPRS Redpiler."""

    def __init__(self, Configuration: MchprsConfiguration | None = None) -> None:
        self.Configuration = Configuration or MchprsConfiguration()

    def Validate(
        self,
        *,
        Fixture: PhysicalFixtureArtifact,
        LogicPath: Path,
        ProgressCallback: Callable[[PhysicalValidationProgress], None] | None = None,
    ) -> PhysicalValidationResult:
        StartedAt = monotonic()
        try:
            from RedstoneCompiler.RustRouting import ValidateMchprsFixture
        except (ImportError, AttributeError) as Error:
            return PhysicalValidationResult(
                Status="infrastructure-failure",
                Backend="mchprs-redpiler-fe217210",
                RuntimeSeconds=monotonic() - StartedAt,
                Diagnostics={"Reason": "native-mchprs-backend-unavailable", "Error": str(Error)},
            )
        try:
            FixtureJson = Fixture.Path.read_text(encoding="utf-8")
            LogicJson = Path(LogicPath).read_text(encoding="utf-8")
            FixtureDocument = json.loads(FixtureJson)
            InputNames = [str(Value["Name"]) for Value in FixtureDocument["Inputs"]]
            WideAssignments = []
            if len(InputNames) > self.Configuration.ExhaustiveInputLimit:
                WideAssignments = [
                    PackAssignment(InputNames, Assignment)
                    for Assignment in BuildValidationAssignments(
                        InputNames,
                        ExhaustiveLimit=self.Configuration.ExhaustiveInputLimit,
                    )
                ]
            TotalVectors = (
                1 << len(InputNames)
                if len(InputNames) <= self.Configuration.ExhaustiveInputLimit
                else len(WideAssignments)
            )

            def ReportNativeProgress(Completed: int, Total: int) -> None:
                if ProgressCallback is not None:
                    ProgressCallback(PhysicalValidationProgress(
                        Completed=int(Completed),
                        Total=int(Total),
                        Stage="MCHPRS exhaustive physical validation",
                        Backend="mchprs-redpiler-fe217210",
                    ))

            ReportNativeProgress(0, TotalVectors)

            Response = json.loads(ValidateMchprsFixture(
                FixtureJson,
                LogicJson,
                self.Configuration.ExhaustiveInputLimit,
                self.Configuration.MaximumSettleTicks,
                WideAssignments,
                ReportNativeProgress if ProgressCallback is not None else None,
            ))
        except Exception as Error:
            return PhysicalValidationResult(
                Status="infrastructure-failure",
                Backend="mchprs-redpiler-fe217210",
                RuntimeSeconds=monotonic() - StartedAt,
                Diagnostics={"Reason": "native-mchprs-validation-failed", "Error": str(Error)},
            )
        Status = str(Response.get("Status", "infrastructure-failure"))
        if Status not in {"passed", "mismatch", "timeout", "infrastructure-failure"}:
            Status = "infrastructure-failure"
        return PhysicalValidationResult(
            Status=Status,
            Backend=str(Response.get("Backend", "mchprs-redpiler-fe217210")),
            RuntimeSeconds=float(Response.get("RuntimeSeconds", monotonic() - StartedAt)),
            Diagnostics=dict(Response.get("Diagnostics", {})),
        )
