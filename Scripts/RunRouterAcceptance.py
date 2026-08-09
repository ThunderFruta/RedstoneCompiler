#!/usr/bin/env python3
"""Run and judge the physical router regression matrix sequentially."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import importlib
import json
from math import isclose, isfinite
import os
from pathlib import Path
import platform
import runpy
import signal
import shlex
from statistics import median
import subprocess
import sys
from time import monotonic
from typing import Any, Callable


RepositoryRoot = Path(__file__).resolve().parents[1]
# The compiler's routing deadline begins at placement-flow entry, while this
# process timer also includes frontend startup and typed-artifact publication.
# Capture may explicitly request this bounded evidence-publication grace. It
# never applies to comparison/normal runs or changes the acceptance ceiling.
SubprocessDeadlineGraceSeconds = 2.0
DefaultRoutingPublicationReserveSeconds = SubprocessDeadlineGraceSeconds
# The compiler owns the routing deadline and needs a short, fixed interval to
# publish either the final litematic or its typed failure artifact.  Do not
# race that publication with the harness wall-ceiling kill.  This grace never
# relaxes acceptance: measured runtime is still checked against each case's
# immutable RuntimeCeilingSeconds.
SubprocessFinalizationGraceSeconds = 5.0
MaximumDeadlineOverrunSeconds = 1.0
MaximumRuntimeRegressionFraction = 0.05
MaximumRuntimeSpreadFraction = 0.05
RequiredRegressionRoutingThreads = 16
if str(RepositoryRoot) not in sys.path:
    sys.path.insert(0, str(RepositoryRoot))


@dataclass(frozen=True)
class AcceptanceCase:
    """One circuit and its immutable acceptance requirements."""

    Name: str
    ExamplePath: Path
    TopModule: str
    RequiredRuns: int
    TruthTableRows: int
    RuntimeCeilingSeconds: float
    MaximumOverflowPeak: int = 1
    NeedsExactInterfaceProof: bool = False
    PublicationReserveSeconds: float = (
        DefaultRoutingPublicationReserveSeconds
    )

    def __post_init__(self) -> None:
        if (
            not isfinite(self.RuntimeCeilingSeconds)
            or self.RuntimeCeilingSeconds <= 0.0
        ):
            raise ValueError("runtime ceiling must be finite and positive")
        if (
            not isfinite(self.PublicationReserveSeconds)
            or self.PublicationReserveSeconds <= 0.0
        ):
            raise ValueError("publication reserve must be finite and positive")
        if self.PublicationReserveSeconds >= self.RuntimeCeilingSeconds:
            raise ValueError(
                "publication reserve must be below the runtime ceiling"
            )

    @property
    def RoutingDeadlineSeconds(self) -> float:
        """Return the bounded router budget inside the wall-clock ceiling."""
        return self.RuntimeCeilingSeconds - self.PublicationReserveSeconds

    def ToDictionary(self) -> dict[str, object]:
        Result = asdict(self)
        Result["ExamplePath"] = str(self.ExamplePath)
        Result["RoutingDeadlineSeconds"] = self.RoutingDeadlineSeconds
        return Result


AcceptanceCases = (
    AcceptanceCase(
        Name="FullAdder",
        ExamplePath=Path("Examples/FullAdder.sv"),
        TopModule="FullAdder",
        RequiredRuns=5,
        TruthTableRows=8,
        RuntimeCeilingSeconds=10.0,
    ),
    AcceptanceCase(
        Name="RippleCarryAdder4",
        ExamplePath=Path("Examples/RippleCarryAdder4.sv"),
        TopModule="RippleCarryAdder4",
        RequiredRuns=3,
        TruthTableRows=512,
        RuntimeCeilingSeconds=25.0,
    ),
    AcceptanceCase(
        Name="RippleCarryAdder8",
        ExamplePath=Path("Examples/RippleCarryAdder8.sv"),
        TopModule="RippleCarryAdder8",
        RequiredRuns=3,
        TruthTableRows=131_072,
        RuntimeCeilingSeconds=30.0,
    ),
    AcceptanceCase(
        Name="CarryLookaheadAdder4",
        ExamplePath=Path("Examples/CarryLookaheadAdder4.sv"),
        TopModule="CarryLookaheadAdder4",
        RequiredRuns=2,
        TruthTableRows=512,
        RuntimeCeilingSeconds=120.0,
        NeedsExactInterfaceProof=True,
    ),
)

RegressionCaseNames = frozenset({
    "FullAdder",
    "RippleCarryAdder4",
    "RippleCarryAdder8",
})
ExtendedCaseNames = frozenset(
    Case.Name
    for Case in AcceptanceCases
    if Case.NeedsExactInterfaceProof
)
BaselineSchemaVersion = "router-regression-baseline-v1"
FirstValidBaselineSchemaVersion = "router-first-valid-baseline-v1"
AcceptanceManifestSchemaVersion = "router-acceptance-manifest-v2"
ExactInterfaceProofSchemaVersion = (
    "compatibility-exact-interface-proof-v1"
)
DefaultExactInterfaceProofPath = (
    RepositoryRoot / "Tests/Fixtures/CompatibilityExactInterfaceProof.json"
)
ExactInterfaceProofCheckpointField = "ExactInterfaceProofCheckpoint"
CanonicalArithmeticDigests = {
    "FullAdder": (
        "2ac95308cc8e1566382817333b1995734fcd61ae6890c189767935cec96bbd12"
    ),
    "RippleCarryAdder4": (
        "2432227f6d42a9b034409f23f0e8471e9b254a528160c3642d8ff53715bf4ff5"
    ),
    "RippleCarryAdder8": (
        "6bf3f999868950799e2ce167870e8453e4bb9d0a80b5a2f4fab776a1eaf30b16"
    ),
    "CarryLookaheadAdder4": (
        "2432227f6d42a9b034409f23f0e8471e9b254a528160c3642d8ff53715bf4ff5"
    ),
}

RouteMetricFields = (
    "Length",
    "Bends",
    "Vias",
    "ReroutedNets",
    "RoutingPasses",
    "Conflicts",
    "OverflowPeak",
    "AccessOverflowPeak",
    "AccessOverflowCells",
    "PerNetLength",
    "MaximumNetLengthShare",
)

FootprintMetricFields = (
    "Footprint",
    "FullFootprint",
    "ExactNonAirBlocks",
)
DimensionMetricFields = (
    "Width",
    "Height",
    "Depth",
)
DeterministicEvidenceFields = (
    "PlacementFingerprint",
    "CandidateFingerprint",
    "ResourceGraphFingerprint",
    "EffectiveWorkFingerprint",
    "OwnershipCounts",
    "RouteMetrics",
    "FootprintMetrics",
    "LitematicComposition",
    "SimulationBackend",
    "StableArtifactSha256",
    "EmittedDesignSha256",
    "TruthTableArithmeticSha256",
    "TruthTableSimulationSha256",
)
PerfBlockSchemaVersion = "router-performance-v1"
# Baseline capture remains pinned to the frozen pre-change policy. Ordinary
# acceptance and comparison target the current implementation policy.
BaselinePolicyVersion = "physical-design-v15-compact-boundaries"
CurrentPolicyVersion = "physical-design-v16-reconvergent-access"
# Backwards-compatible constants for callers of the earlier harness surface.
AcceptedPolicyVersion = CurrentPolicyVersion
CandidatePolicyVersion = CurrentPolicyVersion


def _NormalizeNumericMetrics(Value: object) -> dict[str, object]:
    if not isinstance(Value, dict):
        return {}
    return {
        str(Name): Item
        for Name, Item in sorted(Value.items(), key=lambda Pair: str(Pair[0]))
        if isinstance(Item, (int, float)) and not isinstance(Item, bool)
    }


def _NormalizeDiagnosticsSample(Value: object) -> dict[str, object]:
    if not isinstance(Value, dict):
        return {}
    return {
        str(Name): {
            str(Key): Number
            for Key, Number in sorted(
                Values.items(), key=lambda Pair: str(Pair[0])
            )
            if isinstance(Number, (int, float)) and not isinstance(Number, bool)
        }
        for Name, Values in sorted(Value.items(), key=lambda Pair: str(Pair[0]))
        if isinstance(Values, dict)
    }


def BuildPerfTelemetry(RouterReliability: dict[str, object] | None) -> dict[str, object]:
    """Build a deterministic perf block for acceptance evidence persistence."""
    if not isinstance(RouterReliability, dict):
        return {"SchemaVersion": PerfBlockSchemaVersion}

    StageTimingsSeconds = RouterReliability.get("StageTimingsSeconds", {})
    if not isinstance(StageTimingsSeconds, dict):
        StageTimingsSeconds = {}

    NativeWork = RouterReliability.get("NativeWork", {})
    if not isinstance(NativeWork, dict):
        NativeWork = {}

    NativeBatching = NativeWork.get("Batching", {})
    if not isinstance(NativeBatching, dict):
        NativeBatching = {}
    RequestCounts = NativeWork.get("RequestCounts", {})
    if not isinstance(RequestCounts, dict):
        RequestCounts = {}
    CompletedWork = NativeWork.get("CompletedWork", {})
    if not isinstance(CompletedWork, dict):
        CompletedWork = {}

    CandidateDiagnostics = NativeBatching.get("CandidateDiagnostics", {})
    if not isinstance(CandidateDiagnostics, dict):
        CandidateDiagnostics = {}

    Deadline = RouterReliability.get("Deadline", {})
    if not isinstance(Deadline, dict):
        Deadline = {}

    CandidateDiagnosticsSummary = {
        "SignalCount": len(CandidateDiagnostics),
        "SignalsWithDeferredRequests": sum(
            1
            for Values in CandidateDiagnostics.values()
            if isinstance(Values, dict) and Values.get("DeferredRequests", 0) > 0
        ),
    }

    return {
        "SchemaVersion": PerfBlockSchemaVersion,
        "StageTimingsSeconds": _NormalizeNumericMetrics(StageTimingsSeconds),
        "NativeWork": {
            "Batching": {
                **_NormalizeNumericMetrics(NativeBatching),
                "CandidateDiagnostics": _NormalizeDiagnosticsSample(
                    CandidateDiagnostics
                ),
            },
            "RequestCounts": _NormalizeNumericMetrics(RequestCounts),
            "CompletedWork": _NormalizeNumericMetrics(CompletedWork),
            "Assignment": _NormalizeNumericMetrics(
                NativeWork.get("Assignment", {})
                if isinstance(NativeWork.get("Assignment"), dict)
                else {}
            ),
            "CandidateDiagnosticsSummary": CandidateDiagnosticsSummary,
        },
        "Deadline": {
            str(Name): Value
            for Name, Value in sorted(Deadline.items(), key=lambda Pair: str(Pair[0]))
        },
    }


@dataclass(frozen=True)
class AcceptanceConfiguration:
    """Resolved paths and controls for one sequential acceptance session."""

    RepositoryRoot: Path
    OutputRoot: Path
    DateLabel: str
    PythonExecutable: Path
    DryRun: bool = False
    RoutingThreads: int | None = None
    ExpectedSeed: int = 0
    BaselineMode: str | None = None
    BaselinePath: Path | None = None
    ExpectedPolicyVersion: str | None = None
    CaptureTimeoutGraceSeconds: float = 0.0
    IncludeCla4: bool = False

    def __post_init__(self) -> None:
        if self.BaselineMode not in {None, "capture", "compare"}:
            raise ValueError("baseline mode must be capture, compare, or None")
        if (self.BaselineMode is None) != (self.BaselinePath is None):
            raise ValueError("baseline mode and baseline path must be set together")
        if self.IncludeCla4 and self.BaselineMode == "capture":
            raise ValueError(
                "--include-cla4 cannot be combined with baseline capture"
            )
        if (
            not isfinite(self.CaptureTimeoutGraceSeconds)
            or self.CaptureTimeoutGraceSeconds < 0.0
            or self.CaptureTimeoutGraceSeconds
            > SubprocessDeadlineGraceSeconds
        ):
            raise ValueError(
                "capture timeout grace must be between zero and "
                f"{SubprocessDeadlineGraceSeconds:g} seconds"
            )
        if (
            self.CaptureTimeoutGraceSeconds > 0.0
            and self.BaselineMode != "capture"
        ):
            raise ValueError(
                "subprocess timeout grace is available only during "
                "explicit baseline capture"
            )
        ExpectedPolicyVersion = self.ExpectedPolicyVersion
        if ExpectedPolicyVersion is None:
            ExpectedPolicyVersion = (
                BaselinePolicyVersion
                if self.BaselineMode == "capture"
                else CurrentPolicyVersion
            )
            object.__setattr__(
                self,
                "ExpectedPolicyVersion",
                ExpectedPolicyVersion,
            )
        if (
            self.BaselineMode is not None
            and self.RoutingThreads != RequiredRegressionRoutingThreads
        ):
            raise ValueError(
                "baseline capture/compare requires exactly "
                f"{RequiredRegressionRoutingThreads} routing threads"
            )
        RequiredPolicyVersion = {
            "capture": BaselinePolicyVersion,
            "compare": CurrentPolicyVersion,
            None: ExpectedPolicyVersion,
        }[self.BaselineMode]
        if ExpectedPolicyVersion != RequiredPolicyVersion:
            raise ValueError(
                f"{self.BaselineMode} mode requires policy version "
                f"{RequiredPolicyVersion}"
            )
        if not ExpectedPolicyVersion:
            raise ValueError("expected policy version must not be empty")

    @property
    def RecoveryRoot(self) -> Path:
        SessionName = {
            "capture": "BaselineCapture",
            "compare": "CandidateComparison",
            None: "StandaloneAcceptance",
        }[self.BaselineMode]
        return (
            self.OutputRoot
            / self.DateLabel
            / "RouterRegression"
            / SessionName
        ).resolve(strict=False)

    @property
    def ManifestPath(self) -> Path:
        return self.RecoveryRoot / "AcceptanceManifest.json"


@dataclass(frozen=True)
class AcceptanceCommandResult:
    """Captured result of one compiler process."""

    ReturnCode: int
    Stdout: str
    Stderr: str
    RuntimeSeconds: float
    TimedOut: bool = False


def UtcNow() -> str:
    return datetime.now(timezone.utc).isoformat()


def DecodeProcessText(Value: object) -> str:
    if Value is None:
        return ""
    if isinstance(Value, bytes):
        return Value.decode("utf-8", errors="replace")
    return str(Value)


def RunCompilerCommand(
    *,
    Command: list[str],
    WorkingDirectory: Path,
    Environment: dict[str, str],
    TimeoutSeconds: float,
) -> AcceptanceCommandResult:
    """Run one compiler process with a hard wall-clock ceiling."""
    Started = monotonic()
    Process = subprocess.Popen(
        Command,
        cwd=WorkingDirectory,
        env=Environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        Stdout, Stderr = Process.communicate(
            timeout=TimeoutSeconds,
        )
        return AcceptanceCommandResult(
            ReturnCode=int(Process.returncode or 0),
            Stdout=Stdout,
            Stderr=Stderr,
            RuntimeSeconds=monotonic() - Started,
        )
    except subprocess.TimeoutExpired as Error:
        try:
            os.killpg(Process.pid, signal.SIGKILL)
        except (AttributeError, OSError):
            Process.kill()
        FinalStdout, FinalStderr = Process.communicate()
        return AcceptanceCommandResult(
            ReturnCode=124,
            Stdout=(
                FinalStdout
                if FinalStdout
                else DecodeProcessText(Error.stdout)
            ),
            Stderr=(
                (
                    FinalStderr
                    if FinalStderr
                    else DecodeProcessText(Error.stderr)
                )
                + "\nAcceptance harness terminated the run at its "
                + f"{TimeoutSeconds:.3f}s wall-clock ceiling.\n"
            ),
            RuntimeSeconds=monotonic() - Started,
            TimedOut=True,
        )


def ReadSourceState(RepoRoot: Path) -> dict[str, object]:
    """Read the exact Git revision and dirty marker without changing the tree."""
    try:
        Revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=RepoRoot,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        Status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=RepoRoot,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return {"Revision": Revision, "Dirty": bool(Status.strip())}
    except (OSError, subprocess.SubprocessError):
        return {
            "Revision": os.environ.get("RC_SOURCE_REVISION", "unknown"),
            "Dirty": os.environ.get("RC_SOURCE_DIRTY", "unknown"),
        }


def ReadOptionalText(PathValue: Path) -> str | None:
    """Read one small system identity file without making it mandatory."""
    try:
        return PathValue.read_text(
            encoding="utf-8",
            errors="replace",
        ).strip()
    except OSError:
        return None


def ParseCpuInfo(CpuInfoPath: Path = Path("/proc/cpuinfo")) -> dict[str, str]:
    """Return the first processor block using normalized field names."""
    Text = ReadOptionalText(CpuInfoPath)
    if not Text:
        return {}
    Result: dict[str, str] = {}
    for Line in Text.splitlines():
        if not Line.strip() and Result:
            break
        Name, Separator, Value = Line.partition(":")
        if Separator and Value.strip():
            Result[Name.strip().lower()] = Value.strip()
    return Result


def SelectCpuModel(
    *,
    CpuInfo: dict[str, str],
    PlatformProcessor: str,
    Architecture: str,
) -> str:
    """Prefer the descriptive processor model over generic ISA labels."""
    GenericValues = {
        "",
        "unknown",
        Architecture.strip().lower(),
        "x86_64",
        "amd64",
        "i386",
        "i686",
        "aarch64",
        "arm64",
    }
    Candidates = (
        CpuInfo.get("model name", ""),
        CpuInfo.get("hardware", ""),
        CpuInfo.get("processor", ""),
        PlatformProcessor,
        Architecture,
    )
    for Candidate in Candidates:
        Normalized = Candidate.strip()
        if Normalized and Normalized.lower() not in GenericValues:
            return Normalized
    return next(
        (
            Candidate.strip()
            for Candidate in Candidates
            if Candidate.strip()
        ),
        "unknown",
    )


def ReadCpuProfile(
    CpuInfoPath: Path = Path("/proc/cpuinfo"),
) -> dict[str, object]:
    """Return robust hardware identity fields used by speed comparisons."""
    Architecture = platform.machine()
    CpuInfo = ParseCpuInfo(CpuInfoPath)
    Model = SelectCpuModel(
        CpuInfo=CpuInfo,
        PlatformProcessor=platform.processor(),
        Architecture=Architecture,
    )
    return {
        "Architecture": Architecture,
        "LogicalCpuCount": os.cpu_count(),
        "Model": Model,
        "VendorIdentifier": CpuInfo.get(
            "vendor_id",
            CpuInfo.get("cpu implementer"),
        ),
        "CpuFamily": CpuInfo.get("cpu family", CpuInfo.get("cpu architecture")),
        "ModelNumber": CpuInfo.get("model", CpuInfo.get("cpu part")),
        "Stepping": CpuInfo.get("stepping", CpuInfo.get("cpu revision")),
    }


def ReadCpuAffinity() -> dict[str, object]:
    """Capture the exact processor set available to benchmark children."""
    try:
        CpuIds = sorted(int(Value) for Value in os.sched_getaffinity(0))
        Source = "sched_getaffinity"
    except (AttributeError, OSError):
        Count = os.cpu_count()
        CpuIds = list(range(Count)) if isinstance(Count, int) else []
        Source = "os.cpu_count"
    return {
        "Source": Source,
        "CpuCount": len(CpuIds),
        "CpuIds": CpuIds,
    }


def ReadCpuGovernorProfile(
    Affinity: dict[str, object],
    SysCpuRoot: Path = Path("/sys/devices/system/cpu"),
) -> dict[str, object]:
    """Capture scaling governors for every CPU in the effective affinity."""
    CpuIds = Affinity.get("CpuIds")
    if not isinstance(CpuIds, list):
        CpuIds = []
    GovernorByCpu: dict[str, str] = {}
    for CpuId in CpuIds:
        if not isinstance(CpuId, int) or isinstance(CpuId, bool):
            continue
        Governor = ReadOptionalText(
            SysCpuRoot
            / f"cpu{CpuId}"
            / "cpufreq"
            / "scaling_governor"
        )
        GovernorByCpu[str(CpuId)] = Governor or "unavailable"
    return {
        "GovernorByCpu": GovernorByCpu,
        "Governors": sorted(set(GovernorByCpu.values())),
    }


def ReadCgroupCpuQuotaProfile(
    ProcCgroupPath: Path = Path("/proc/self/cgroup"),
    CgroupRoot: Path = Path("/sys/fs/cgroup"),
) -> dict[str, object]:
    """Resolve the effective cgroup CPU quota without recording volatile paths."""
    CgroupText = ReadOptionalText(ProcCgroupPath)
    if not CgroupText:
        return {
            "Schema": "unavailable",
            "QuotaLimited": None,
            "EffectiveQuotaCpuCount": None,
        }
    Lines = [
        Line.split(":", 2)
        for Line in CgroupText.splitlines()
        if Line.count(":") >= 2
    ]
    VersionTwo = next(
        (
            Parts
            for Parts in Lines
            if Parts[0] == "0" and Parts[1] == ""
        ),
        None,
    )
    QuotaCounts: list[float] = []
    if VersionTwo is not None:
        RelativePath = Path(VersionTwo[2].lstrip("/"))
        Current = CgroupRoot / RelativePath
        while Current == CgroupRoot or CgroupRoot in Current.parents:
            CpuMax = ReadOptionalText(Current / "cpu.max")
            if CpuMax:
                Values = CpuMax.split()
                if len(Values) >= 2 and Values[0] != "max":
                    try:
                        Quota = float(Values[0])
                        Period = float(Values[1])
                        if Quota > 0.0 and Period > 0.0:
                            QuotaCounts.append(Quota / Period)
                    except ValueError:
                        pass
            if Current == CgroupRoot:
                break
            Current = Current.parent
        EffectiveQuota = min(QuotaCounts) if QuotaCounts else None
        return {
            "Schema": "cgroup-v2",
            "QuotaLimited": EffectiveQuota is not None,
            "EffectiveQuotaCpuCount": (
                round(EffectiveQuota, 9)
                if EffectiveQuota is not None
                else None
            ),
        }

    VersionOne = next(
        (
            Parts
            for Parts in Lines
            if "cpu" in Parts[1].split(",")
        ),
        None,
    )
    if VersionOne is None:
        return {
            "Schema": "unavailable",
            "QuotaLimited": None,
            "EffectiveQuotaCpuCount": None,
        }
    RelativePath = Path(VersionOne[2].lstrip("/"))
    CpuRoot = CgroupRoot / "cpu" / RelativePath
    QuotaText = ReadOptionalText(CpuRoot / "cpu.cfs_quota_us")
    PeriodText = ReadOptionalText(CpuRoot / "cpu.cfs_period_us")
    try:
        Quota = float(QuotaText) if QuotaText is not None else -1.0
        Period = float(PeriodText) if PeriodText is not None else -1.0
    except ValueError:
        Quota = -1.0
        Period = -1.0
    EffectiveQuota = (
        Quota / Period if Quota > 0.0 and Period > 0.0 else None
    )
    return {
        "Schema": "cgroup-v1",
        "QuotaLimited": EffectiveQuota is not None,
        "EffectiveQuotaCpuCount": (
            round(EffectiveQuota, 9)
            if EffectiveQuota is not None
            else None
        ),
    }


def BuildCpuExecutionProfile() -> dict[str, object]:
    """Capture scheduler controls that materially affect runtime."""
    Affinity = ReadCpuAffinity()
    return {
        "Affinity": Affinity,
        "Governors": ReadCpuGovernorProfile(Affinity),
        "CgroupCpuQuota": ReadCgroupCpuQuotaProfile(),
    }


def BuildLoadProfile(
    CpuExecutionProfile: dict[str, object],
) -> dict[str, object]:
    """Normalize host load and reduce it to a stable compatibility class."""
    try:
        LoadAverages = tuple(float(Value) for Value in os.getloadavg())
    except (AttributeError, OSError):
        LoadAverages = (0.0, 0.0, 0.0)
    AffinityCount = ReadNested(
        CpuExecutionProfile,
        "Affinity",
        "CpuCount",
    )
    QuotaCount = ReadNested(
        CpuExecutionProfile,
        "CgroupCpuQuota",
        "EffectiveQuotaCpuCount",
    )
    CapacityCandidates = [
        float(Value)
        for Value in (AffinityCount, QuotaCount)
        if (
            isinstance(Value, (int, float))
            and not isinstance(Value, bool)
            and float(Value) > 0.0
        )
    ]
    EffectiveCapacity = min(CapacityCandidates) if CapacityCandidates else 1.0
    Normalized = tuple(
        Value / EffectiveCapacity for Value in LoadAverages
    )
    MaximumNormalizedLoad = max(Normalized)
    LoadClass = (
        "quiet"
        if MaximumNormalizedLoad <= 0.25
        else "moderate"
        if MaximumNormalizedLoad <= 0.75
        else "busy"
    )
    return {
        "LoadAverage1Minute": round(LoadAverages[0], 6),
        "LoadAverage5Minutes": round(LoadAverages[1], 6),
        "LoadAverage15Minutes": round(LoadAverages[2], 6),
        "EffectiveCpuCapacity": round(EffectiveCapacity, 9),
        "NormalizedLoadAverage1Minute": round(Normalized[0], 9),
        "NormalizedLoadAverage5Minutes": round(Normalized[1], 9),
        "NormalizedLoadAverage15Minutes": round(Normalized[2], 9),
        "CompatibilityClass": LoadClass,
    }


def ReadConfiguredPythonProfile(
    PythonExecutable: Path,
    RepositoryPath: Path = RepositoryRoot,
) -> dict[str, object]:
    """Query the interpreter that will actually launch benchmark children."""
    if not PythonExecutable.is_file():
        Result = {
            "ProfileSource": "harness-fallback",
            "PythonVersion": platform.python_version(),
            "PythonImplementation": platform.python_implementation(),
            "PythonExecutable": str(PythonExecutable),
            "PythonPrefix": sys.prefix,
            "PythonBasePrefix": sys.base_prefix,
            "Platform": platform.platform(),
            "CpuProfile": ReadCpuProfile(),
            "CpuExecutionProfile": BuildCpuExecutionProfile(),
        }
        Result["LoadProfile"] = BuildLoadProfile(
            Result["CpuExecutionProfile"]
        )
        return Result
    ProbeSource = """
import json
import platform
import sys
from Scripts.RunRouterAcceptance import (
    BuildCpuExecutionProfile,
    BuildLoadProfile,
    ReadCpuProfile,
)

CpuExecutionProfile = BuildCpuExecutionProfile()
print(json.dumps({
    "ProfileSource": "configured-child",
    "PythonVersion": platform.python_version(),
    "PythonImplementation": platform.python_implementation(),
    "PythonExecutable": sys.executable,
    "PythonPrefix": sys.prefix,
    "PythonBasePrefix": sys.base_prefix,
    "Platform": platform.platform(),
    "CpuProfile": ReadCpuProfile(),
    "CpuExecutionProfile": CpuExecutionProfile,
    "LoadProfile": BuildLoadProfile(CpuExecutionProfile),
}, sort_keys=True))
"""
    Completed = subprocess.run(
        [str(PythonExecutable), "-c", ProbeSource],
        cwd=RepositoryPath,
        capture_output=True,
        text=True,
        timeout=10.0,
        check=True,
    )
    Loaded = json.loads(Completed.stdout)
    if not isinstance(Loaded, dict):
        raise ValueError("configured Python profile is not an object")
    return Loaded


def BuildEnvironmentRecord(
    Configuration: AcceptanceConfiguration,
) -> dict[str, object]:
    """Capture reproducibility settings without copying unrelated secrets."""
    PythonProfile = ReadConfiguredPythonProfile(
        Configuration.PythonExecutable,
        Configuration.RepositoryRoot,
    )
    RoutingEnvironment = {
        Name: Value
        for Name, Value in sorted(os.environ.items())
        if Name.startswith("RC_") or Name.startswith("RCS_")
    }
    RoutingEnvironment["PYTHONHASHSEED"] = str(Configuration.ExpectedSeed)
    if Configuration.RoutingThreads is not None:
        RoutingEnvironment["RC_ROUTING_THREADS"] = str(
            Configuration.RoutingThreads
        )
    return {
        **PythonProfile,
        "RequestedPythonExecutable": str(Configuration.PythonExecutable),
        "WorkingDirectory": str(Configuration.RepositoryRoot),
        "PolicySeed": Configuration.ExpectedSeed,
        "RoutingThreads": Configuration.RoutingThreads,
        "RoutingEnvironment": RoutingEnvironment,
    }


def BuildChildEnvironment(
    Configuration: AcceptanceConfiguration,
) -> dict[str, str]:
    Result = dict(os.environ)
    Result["PYTHONHASHSEED"] = str(Configuration.ExpectedSeed)
    if Configuration.RoutingThreads is not None:
        Result["RC_ROUTING_THREADS"] = str(Configuration.RoutingThreads)
    return Result


def BuildRunArtifacts(RunDirectory: Path, RunName: str) -> dict[str, Path]:
    OutputPath = RunDirectory / f"{RunName}.litematic"
    return {
        "RunDirectory": RunDirectory,
        "Schematic": OutputPath,
        "TruthTable": OutputPath.with_suffix(".TruthTable.txt"),
        "PhysicalDesign": OutputPath.with_suffix(".PhysicalDesign.json"),
        "RoutingFailure": OutputPath.with_suffix(".RoutingFailure.json"),
        "Diagram": OutputPath.with_suffix(".Nand.json"),
        "Dot": OutputPath.with_suffix(".Nand.dot"),
        "Stdout": RunDirectory / "stdout.log",
        "Stderr": RunDirectory / "stderr.log",
        "Workdir": RunDirectory / "Frontend",
    }


def FindExactClusterInterfaceSolve(
    Value: object,
) -> dict[str, object] | None:
    """Find the single terminal exact-interface result in a failure artifact."""
    Matches: list[dict[str, object]] = []

    def Visit(Current: object) -> None:
        if isinstance(Current, dict):
            if (
                Current.get("ProofFingerprint")
                and (
                    Current.get("Result")
                    == "exact-cluster-interface-solve"
                    or "StateProofs" in Current
                )
            ):
                Matches.append(Current)
            for Child in Current.values():
                Visit(Child)
        elif isinstance(Current, list):
            for Child in Current:
                Visit(Child)

    Visit(Value)
    Unique = {
        (
            str(Match.get("ProofFingerprint", "")),
            int(Match.get("AttemptedStateCount", 0)),
        ): Match
        for Match in Matches
    }
    if len(Unique) != 1:
        return None
    return next(iter(Unique.values()))


def EvaluateExactInterfaceProofCheckpoint(
    Artifacts: dict[str, Path],
    FixturePath: Path = DefaultExactInterfaceProofPath,
) -> dict[str, object]:
    """Accept either a completed deterministic artifact or a frozen exact proof."""
    Completed = all(
        Artifacts[Name].is_file()
        for Name in ("Schematic", "TruthTable", "PhysicalDesign")
    ) and not Artifacts["RoutingFailure"].is_file()
    if Completed:
        return {
            "Accepted": True,
            "Outcome": "completed-artifact",
            "ProofFingerprint": None,
            "FallbackOrThrashingObserved": False,
            "Failures": [],
        }

    Failures: list[str] = []
    try:
        Fixture = json.loads(FixturePath.read_text(encoding="utf-8"))
    except Exception as Error:
        return {
            "Accepted": False,
            "Outcome": "invalid-fixture",
            "ProofFingerprint": None,
            "FallbackOrThrashingObserved": False,
            "Failures": [
                f"could not read exact-interface proof fixture: {Error}"
            ],
        }
    if (
        Fixture.get("SchemaVersion") != ExactInterfaceProofSchemaVersion
    ):
        Failures.append("exact proof fixture schema mismatch")
    if not Artifacts["RoutingFailure"].is_file():
        Failures.append("neither completed artifacts nor routing proof exists")
        FailureDocument: object = {}
    else:
        try:
            FailureDocument = json.loads(
                Artifacts["RoutingFailure"].read_text(encoding="utf-8")
            )
        except Exception as Error:
            Failures.append(
                f"could not read exact-interface routing proof: {Error}"
            )
            FailureDocument = {}

    ExactSolve = FindExactClusterInterfaceSolve(FailureDocument)
    Expected = Fixture.get("ExactProof", {})
    if not isinstance(Expected, dict):
        Expected = {}
        Failures.append("exact proof fixture has no ExactProof object")
    if ExactSolve is None:
        Failures.append("routing failure has no unique exact-interface proof")
        ExactSolve = {}

    ExpectedStates = Expected.get("StateProofs", [])
    ObservedStates = ExactSolve.get("StateProofs", [])
    if not isinstance(ExpectedStates, list):
        ExpectedStates = []
    if not isinstance(ObservedStates, list):
        ObservedStates = []

    def BuildStateIdentity(Value: object) -> tuple[object, ...]:
        if not isinstance(Value, dict):
            return ()
        AssignmentFingerprints = tuple(
            sorted(
                str(Fingerprint)
                for Fingerprint in Value.get(
                    "AssignmentFingerprints",
                    (),
                )
                if Fingerprint is not None
            )
        )
        return (
            str(Value.get("PlacementStateFingerprint", "")),
            str(Value.get("Status", "")),
            str(Value.get("TransformFingerprint", "")),
            str(Value.get("OwnershipUnsatCoreFingerprint", "")),
            AssignmentFingerprints,
        )

    ExpectedStateIdentity = sorted(
        [
            {
                "PlacementStateFingerprint": State.get("PlacementStateFingerprint"),
                "Status": State.get("Status"),
                "TransformFingerprint": State.get("TransformFingerprint"),
                "OwnershipUnsatCoreFingerprint": (
                    State.get("OwnershipUnsatCoreFingerprint")
                ),
                "AssignmentFingerprints": tuple(
                    sorted(
                        (
                            str(Fingerprint)
                            for Fingerprint in (
                                State.get(
                                    "AssignmentFingerprints",
                                    (),
                                )
                                or ()
                            )
                            if Fingerprint is not None
                        )
                    )
                ),
            }
            for State in ExpectedStates
            if isinstance(State, dict)
        ],
        key=BuildStateIdentity,
    )
    ObservedStateIdentity = sorted(
        [
            {
                "PlacementStateFingerprint": State.get("PlacementStateFingerprint"),
                "Status": State.get("Status"),
                "TransformFingerprint": State.get("TransformFingerprint"),
                "OwnershipUnsatCoreFingerprint": (
                    State.get("OwnershipUnsatCoreFingerprint")
                ),
                "AssignmentFingerprints": tuple(
                    sorted(
                        (
                            str(Fingerprint)
                            for Fingerprint in (
                                State.get(
                                    "AssignmentFingerprints",
                                    (),
                                )
                                or ()
                            )
                            if Fingerprint is not None
                        )
                    )
                ),
            }
            for State in ObservedStates
            if isinstance(State, dict)
        ],
        key=BuildStateIdentity,
    )
    for Name in (
        "ProofFingerprint",
        "AttemptedStateCount",
        "BroadFallbackAllowed",
    ):
        if ExactSolve.get(Name) != Expected.get(Name):
            Failures.append(f"exact-interface proof mismatch: {Name}")
    if ExactSolve.get("ExecutableRepairAllowed", False) is not False:
        Failures.append("exact-interface proof permits executable repair")
    if ObservedStateIdentity != ExpectedStateIdentity:
        Failures.append("exact-interface proof state identity mismatch")

    Forbidden = {
        str(Value)
        for Value in Fixture.get("ForbiddenExecutableResults", [])
    }
    ObservedExecutableResults: list[str] = []

    def CollectExecutableResults(Current: object) -> None:
        if isinstance(Current, dict):
            Result = Current.get("Result")
            if (
                isinstance(Result, str)
                and Result in Forbidden
                and Current.get("Executable", True) is not False
                and Current.get("ExecutableLegacyRepairCascade", True)
                is not False
            ):
                ObservedExecutableResults.append(Result)
            for Child in Current.values():
                CollectExecutableResults(Child)
        elif isinstance(Current, list):
            for Child in Current:
                CollectExecutableResults(Child)

    CollectExecutableResults(FailureDocument)
    if ObservedExecutableResults:
        Failures.append(
            "exact-interface proof reintroduced executable fallback/thrashing"
        )
    return {
        "Accepted": not Failures,
        "Outcome": "exact-proof",
        "ProofFingerprint": ExactSolve.get("ProofFingerprint"),
        "FallbackOrThrashingObserved": bool(ObservedExecutableResults),
        "ObservedExecutableResults": sorted(set(
            ObservedExecutableResults
        )),
        "Failures": Failures,
    }


def BuildCompilerCommand(
    Configuration: AcceptanceConfiguration,
    Case: AcceptanceCase,
    RunName: str,
    Artifacts: dict[str, Path],
) -> list[str]:
    Command = [
        str(Configuration.PythonExecutable),
        str(Configuration.RepositoryRoot / "Main.py"),
        "--input",
        str((Configuration.RepositoryRoot / Case.ExamplePath).resolve()),
        "--topmodule",
        Case.TopModule,
        "--output",
        str(Artifacts["RunDirectory"]),
        "--outputname",
        RunName,
        "--diagram",
        str(Artifacts["Diagram"]),
        "--workdir",
        str(Artifacts["Workdir"]),
        "--routing-strategy",
        "default",
        "--routing-deadline-seconds",
        str(Case.RoutingDeadlineSeconds),
    ]
    if Configuration.RoutingThreads is not None:
        Command.extend(
            ["--routing-threads", str(Configuration.RoutingThreads)]
        )
    return Command


def BuildFileRecord(Value: Path) -> dict[str, object]:
    Result: dict[str, object] = {
        "Path": str(Value.resolve(strict=False)),
        "Exists": Value.is_file(),
    }
    if Value.is_file():
        Data = Value.read_bytes()
        Result.update({
            "SizeBytes": len(Data),
            "Sha256": sha256(Data).hexdigest(),
        })
    return Result


def BuildRelativeFileRecord(
    RepositoryPath: Path,
    Value: Path,
) -> dict[str, object]:
    """Hash one repository file without embedding checkout-specific paths."""
    Record = BuildFileRecord(Value)
    Record["Path"] = Value.relative_to(RepositoryPath).as_posix()
    return Record


def BuildSourceContentManifest(
    RepositoryPath: Path,
) -> dict[str, object]:
    """Hash compiler sources that can affect physical benchmark results."""
    CandidatePaths: set[Path] = set()
    for RelativeRoot, Pattern in (
        (Path("Compiler"), "*.py"),
        (Path("SVDecoder"), "*.py"),
        (Path("SchemEncoder"), "*.py"),
        (Path("RustRouting/Src"), "*.rs"),
    ):
        Root = RepositoryPath / RelativeRoot
        if Root.is_dir():
            CandidatePaths.update(
                Value
                for Value in Root.rglob(Pattern)
                if Value.is_file()
            )
    for RelativePath in (
        Path("Main.py"),
        Path("pyproject.toml"),
        Path("Scripts/RunRouterAcceptance.py"),
        Path("RedstoneCompiler/__init__.py"),
        Path("Templates/__init__.py"),
        Path("RustRouting/Cargo.toml"),
        Path("RustRouting/Cargo.lock"),
    ):
        Value = RepositoryPath / RelativePath
        if Value.is_file():
            CandidatePaths.add(Value)

    Files = [
        BuildRelativeFileRecord(RepositoryPath, Value)
        for Value in sorted(
            CandidatePaths,
            key=lambda Item: Item.relative_to(RepositoryPath).as_posix(),
        )
    ]
    Encoded = json.dumps(
        Files,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "AggregateSha256": sha256(Encoded).hexdigest(),
        "FileCount": len(Files),
        "Files": Files,
    }


def BuildBenchmarkInputManifest(
    RepositoryPath: Path,
) -> dict[str, object]:
    """Hash every benchmark input independently of source implementation."""
    return {
        Case.Name: BuildRelativeFileRecord(
            RepositoryPath,
            RepositoryPath / Case.ExamplePath,
        )
        for Case in AcceptanceCases
    }


def BuildResolvedTemplateInputManifest(
    RepositoryPath: Path,
    PythonExecutable: Path | None = None,
) -> dict[str, object]:
    """Hash the exact standard-cell litematics selected by the child."""
    RequiredNames = ("Input", "Nand", "Output")
    ResolvedPaths: dict[str, str] = {}
    ResolutionSource = "repository-module"
    if PythonExecutable is not None and PythonExecutable.is_file():
        ProbeSource = """
import json
from Templates import LitematicTemplates

print(json.dumps({
    str(Name): str(PathValue)
    for Name, PathValue in LitematicTemplates.items()
}, sort_keys=True))
"""
        try:
            Completed = subprocess.run(
                [str(PythonExecutable), "-c", ProbeSource],
                cwd=RepositoryPath,
                capture_output=True,
                text=True,
                timeout=10.0,
                check=True,
            )
            Loaded = json.loads(Completed.stdout)
            if not isinstance(Loaded, dict):
                raise ValueError("template probe returned a non-object")
            ResolvedPaths = {
                str(Name): str(Value)
                for Name, Value in Loaded.items()
            }
            ResolutionSource = "configured-child"
        except (
            OSError,
            subprocess.SubprocessError,
            json.JSONDecodeError,
            ValueError,
        ):
            ResolvedPaths = {}
    if not ResolvedPaths:
        TemplateModulePath = RepositoryPath / "Templates" / "__init__.py"
        try:
            Namespace = runpy.run_path(str(TemplateModulePath))
            Loaded = Namespace.get("LitematicTemplates")
            if isinstance(Loaded, dict):
                ResolvedPaths = {
                    str(Name): str(Value)
                    for Name, Value in Loaded.items()
                }
        except (OSError, RuntimeError, ValueError):
            ResolvedPaths = {}

    Records: dict[str, dict[str, object]] = {}
    for Name in RequiredNames:
        RawPath = ResolvedPaths.get(Name)
        if not isinstance(RawPath, str) or not RawPath:
            Records[Name] = {
                "Path": None,
                "Exists": False,
                "WithinRepository": False,
            }
            continue
        UnresolvedPath = Path(RawPath)
        PathValue = (
            UnresolvedPath
            if UnresolvedPath.is_absolute()
            else RepositoryPath / UnresolvedPath
        ).resolve(strict=False)
        if PathValue.is_relative_to(RepositoryPath):
            Record = BuildRelativeFileRecord(RepositoryPath, PathValue)
        else:
            Record = BuildFileRecord(PathValue)
        Record["WithinRepository"] = PathValue.is_relative_to(RepositoryPath)
        Records[Name] = Record
    Encoded = json.dumps(
        Records,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "ResolutionSource": ResolutionSource,
        "AggregateSha256": sha256(Encoded).hexdigest(),
        "Templates": Records,
    }


def BuildNativeExtensionRecord(
    RepositoryPath: Path,
    PythonExecutable: Path | None = None,
) -> dict[str, object]:
    """Import and hash the native extension resolved by the configured child."""
    if PythonExecutable is not None and PythonExecutable.is_file():
        ProbeSource = """
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sys

ModuleName = "RedstoneCompiler.RustRouting"
try:
    Module = importlib.import_module(ModuleName)
    RawPath = getattr(Module, "__file__", None)
    if not isinstance(RawPath, str) or not RawPath:
        raise ValueError("loaded native extension has no __file__")
    LoadedPath = Path(RawPath).resolve(strict=False)
    Data = LoadedPath.read_bytes()
    print(json.dumps({
        "Exists": LoadedPath.is_file(),
        "Loaded": True,
        "Module": ModuleName,
        "Path": str(LoadedPath),
        "SizeBytes": len(Data),
        "Sha256": sha256(Data).hexdigest(),
        "ProbeSource": "configured-child",
        "ProbePythonExecutable": sys.executable,
    }, sort_keys=True))
except Exception as Error:
    print(json.dumps({
        "Exists": False,
        "Loaded": False,
        "Module": ModuleName,
        "Failure": f"could not import native extension: {Error}",
        "ProbeSource": "configured-child",
        "ProbePythonExecutable": sys.executable,
    }, sort_keys=True))
"""
        try:
            Completed = subprocess.run(
                [str(PythonExecutable), "-c", ProbeSource],
                cwd=RepositoryPath,
                capture_output=True,
                text=True,
                timeout=10.0,
                check=True,
            )
            Record = json.loads(Completed.stdout)
            if not isinstance(Record, dict):
                raise ValueError("native-extension probe returned a non-object")
            RawPath = Record.get("Path")
            if isinstance(RawPath, str) and RawPath:
                LoadedPath = Path(RawPath).resolve(strict=False)
                Record["WithinRepository"] = LoadedPath.is_relative_to(
                    RepositoryPath
                )
                if Record["WithinRepository"]:
                    Record["Path"] = LoadedPath.relative_to(
                        RepositoryPath
                    ).as_posix()
            else:
                Record["WithinRepository"] = False
            return Record
        except (
            OSError,
            subprocess.SubprocessError,
            json.JSONDecodeError,
            ValueError,
        ) as Error:
            return {
                "Exists": False,
                "Loaded": False,
                "Module": "RedstoneCompiler.RustRouting",
                "Failure": f"configured child probe failed: {Error}",
                "ProbeSource": "configured-child",
                "ProbePythonExecutable": str(PythonExecutable),
                "WithinRepository": False,
            }

    # Tests and API callers may intentionally provide a nonexistent synthetic
    # interpreter. Preserve their read-only fallback while production
    # capture/compare mode requires the repository venv and takes the branch
    # above.
    try:
        Module = importlib.import_module("RedstoneCompiler.RustRouting")
    except (ImportError, OSError) as Error:
        return {
            "Exists": False,
            "Loaded": False,
            "Module": "RedstoneCompiler.RustRouting",
            "Failure": f"could not import native extension: {Error}",
        }
    RawPath = getattr(Module, "__file__", None)
    if not isinstance(RawPath, str) or not RawPath:
        return {
            "Exists": False,
            "Loaded": True,
            "Module": "RedstoneCompiler.RustRouting",
            "Failure": "loaded native extension has no __file__",
        }
    LoadedPath = Path(RawPath).resolve(strict=False)
    if not LoadedPath.is_file():
        return {
            "Exists": False,
            "Loaded": True,
            "Module": "RedstoneCompiler.RustRouting",
            "Path": str(LoadedPath),
            "Failure": "loaded native extension path is not a file",
        }
    if LoadedPath.is_relative_to(RepositoryPath):
        Record = BuildRelativeFileRecord(RepositoryPath, LoadedPath)
    else:
        Record = BuildFileRecord(LoadedPath)
    Record.update({
        "Loaded": True,
        "Module": "RedstoneCompiler.RustRouting",
        "ProbeSource": "harness-fallback",
        "ProbePythonExecutable": sys.executable,
        "WithinRepository": LoadedPath.is_relative_to(RepositoryPath),
    })
    return Record


def BuildPolicyProvenanceRecord() -> dict[str, object]:
    """Fingerprint the complete immutable policy selected by ``default``."""
    from Compiler.Routing.Policy import (
        PolicyForRoutingStrategy,
        RoutingStrategy,
    )

    Policy = PolicyForRoutingStrategy(RoutingStrategy.Default)
    Snapshot = Policy.ToDictionary()
    Encoded = json.dumps(
        Snapshot,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "PolicyVersion": Policy.PolicyVersion,
        "Seed": Policy.Seed,
        "Sha256": sha256(Encoded).hexdigest(),
        "Snapshot": Snapshot,
    }


def BuildSourceProvenance(
    Configuration: AcceptanceConfiguration,
    SourceState: dict[str, object],
) -> dict[str, object]:
    """Capture source, benchmark, native, and policy provenance."""
    return {
        "SchemaVersion": "router-source-provenance-v1",
        "Git": SourceState,
        "SourceContent": BuildSourceContentManifest(
            Configuration.RepositoryRoot
        ),
        "BenchmarkInputs": BuildBenchmarkInputManifest(
            Configuration.RepositoryRoot
        ),
        "PhysicalTemplates": BuildResolvedTemplateInputManifest(
            Configuration.RepositoryRoot,
            Configuration.PythonExecutable,
        ),
        "NativeExtension": BuildNativeExtensionRecord(
            Configuration.RepositoryRoot,
            Configuration.PythonExecutable,
        ),
        "Policy": BuildPolicyProvenanceRecord(),
        "ExpectedPolicyVersion": Configuration.ExpectedPolicyVersion,
    }


def CanonicalizeNbt(Value: object) -> object:
    """Convert parsed NBT values into stable JSON-compatible structures."""
    if hasattr(Value, "TagType") and hasattr(Value, "Value"):
        return {
            "TagType": int(getattr(Value, "TagType")),
            "Value": CanonicalizeNbt(getattr(Value, "Value")),
        }
    if isinstance(Value, dict):
        return {
            str(Key): CanonicalizeNbt(Item)
            for Key, Item in sorted(Value.items(), key=lambda Pair: str(Pair[0]))
        }
    if isinstance(Value, tuple):
        return [CanonicalizeNbt(Item) for Item in Value]
    if isinstance(Value, list):
        return [CanonicalizeNbt(Item) for Item in Value]
    if isinstance(Value, bytes):
        return {"BytesHex": Value.hex()}
    return Value


def BuildEmittedDesignDigest(SchematicPath: Path) -> str:
    """Hash the emitted regions while excluding timestamps and output names."""
    from SchemEncoder.Writer262 import ReadNbt

    Root = ReadNbt(SchematicPath)
    Regions = Root.get("Regions")
    if Regions is None:
        raise ValueError("litematic contains no Regions compound")
    Encoded = json.dumps(
        CanonicalizeNbt(Regions),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(Encoded).hexdigest()


def BuildLitematicCompositionEvidence(
    SchematicPath: Path,
) -> dict[str, int]:
    """Measure the emitted region independently of physical JSON summaries."""
    from SchemEncoder.Writer262 import LoadTemplate, ReadNbt

    Root = ReadNbt(SchematicPath)
    RegionsTag = Root.get("Regions")
    if RegionsTag is None or RegionsTag.TagType != 10:
        raise ValueError("litematic contains no Regions compound")
    Regions = RegionsTag.Value
    if not isinstance(Regions, dict) or len(Regions) != 1:
        raise ValueError(
            "acceptance litematic must contain exactly one emitted region"
        )
    Template = LoadTemplate(SchematicPath)
    Width, Height, Depth = Template.Size
    return {
        "Width": Width,
        "Height": Height,
        "Depth": Depth,
        "Footprint": Width * Depth,
        "FullFootprint": Width * Height * Depth,
        "ExactNonAirBlocks": len(Template.Blocks),
    }


def BuildTruthTableSemanticEvidence(
    TruthTablePath: Path,
) -> dict[str, object]:
    """Parse and hash truth-table arithmetic semantics, ignoring prose layout."""
    Rows: list[dict[str, object]] = []
    for LineNumber, Line in enumerate(
        TruthTablePath.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        Parts = [Part.strip() for Part in Line.split("|")]
        if len(Parts) != 4 or Parts[3] not in {"PASS", "FAIL"}:
            continue
        BitGroups: list[list[int]] = []
        for Part in Parts[:3]:
            Tokens = Part.split()
            if not Tokens or any(Token not in {"0", "1"} for Token in Tokens):
                raise ValueError(
                    f"invalid truth-table bit row at line {LineNumber}"
                )
            BitGroups.append([int(Token) for Token in Tokens])
        Inputs, Expected, Simulated = BitGroups
        if len(Expected) != len(Simulated):
            raise ValueError(
                f"truth-table output width mismatch at line {LineNumber}"
            )
        Rows.append({
            "Inputs": Inputs,
            "Expected": Expected,
            "Simulated": Simulated,
            "Passed": Parts[3] == "PASS",
        })

    if not Rows:
        raise ValueError("truth table contains no semantic rows")
    InputKeys = [tuple(Row["Inputs"]) for Row in Rows]
    if len(set(InputKeys)) != len(InputKeys):
        raise ValueError("truth table contains duplicate input rows")
    Rows.sort(key=lambda Row: tuple(Row["Inputs"]))
    ArithmeticRows = [
        [Row["Inputs"], Row["Expected"]]
        for Row in Rows
    ]
    SimulationRows = [
        [Row["Inputs"], Row["Simulated"]]
        for Row in Rows
    ]

    def Digest(Value: object) -> str:
        Encoded = json.dumps(
            Value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(Encoded).hexdigest()

    ArithmeticDigest = Digest(ArithmeticRows)
    SimulationDigest = Digest(SimulationRows)
    return {
        "RowCount": len(Rows),
        "AllRowsPassed": all(bool(Row["Passed"]) for Row in Rows),
        "ExpectedMatchesSimulated": ArithmeticDigest == SimulationDigest,
        "ArithmeticResultSha256": ArithmeticDigest,
        "SimulationResultSha256": SimulationDigest,
    }


def ReadNested(Value: object, *Names: str) -> object:
    Current = Value
    for Name in Names:
        if not isinstance(Current, dict) or Name not in Current:
            return None
        Current = Current[Name]
    return Current


def EvaluateRun(
    *,
    Case: AcceptanceCase,
    Process: AcceptanceCommandResult,
    Artifacts: dict[str, Path],
    ExpectedSeed: int,
    ExpectedPolicyVersion: str = CurrentPolicyVersion,
    DesignDigestBuilder: Callable[[Path], str] = BuildEmittedDesignDigest,
    LitematicCompositionEvidenceBuilder: Callable[
        [Path], dict[str, int]
    ] = BuildLitematicCompositionEvidence,
    TruthTableEvidenceBuilder: Callable[
        [Path], dict[str, object]
    ] = BuildTruthTableSemanticEvidence,
) -> tuple[dict[str, object], dict[str, object] | None]:
    """Judge one completed process solely from its result and durable artifacts."""
    Failures: list[str] = []
    ProcessEnvelopeSeconds = (
        Case.RoutingDeadlineSeconds + Case.PublicationReserveSeconds
    )
    ProcessEnvelopeValid = (
        isfinite(Case.RoutingDeadlineSeconds)
        and Case.RoutingDeadlineSeconds > 0.0
        and Case.RoutingDeadlineSeconds < Case.RuntimeCeilingSeconds
        and isclose(
            ProcessEnvelopeSeconds,
            Case.RuntimeCeilingSeconds,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    )
    if not ProcessEnvelopeValid:
        Failures.append("invalid routing deadline/publication envelope")
    DeadlineOverrunSeconds = max(
        0.0,
        Process.RuntimeSeconds - Case.RuntimeCeilingSeconds,
    )
    DeadlineOverrunWithinLimit = (
        DeadlineOverrunSeconds < MaximumDeadlineOverrunSeconds
    )
    if Process.TimedOut:
        Failures.append("process timed out")
    if Process.ReturnCode != 0:
        Failures.append(f"process exited {Process.ReturnCode}")
    if Process.RuntimeSeconds > Case.RuntimeCeilingSeconds:
        Failures.append(
            "wall runtime exceeded ceiling: "
            f"{Process.RuntimeSeconds:.6f}s > {Case.RuntimeCeilingSeconds:.6f}s"
        )
    if not DeadlineOverrunWithinLimit:
        Failures.append(
            "deadline overrun did not stay below "
            f"{MaximumDeadlineOverrunSeconds:.6f}s: "
            f"{DeadlineOverrunSeconds:.6f}s"
        )

    for Name in ("Schematic", "TruthTable", "PhysicalDesign"):
        if not Artifacts[Name].is_file():
            Failures.append(f"missing required artifact: {Name}")
    if Artifacts["RoutingFailure"].is_file():
        Failures.append("routing failure artifact exists")

    TruthTableSemantics: dict[str, object] | None = None
    if Artifacts["TruthTable"].is_file():
        try:
            TruthTableSemantics = TruthTableEvidenceBuilder(
                Artifacts["TruthTable"]
            )
            if not isinstance(TruthTableSemantics, dict):
                raise ValueError(
                    "truth-table evidence builder returned a non-object"
                )
            if (
                TruthTableSemantics.get("RowCount")
                != Case.TruthTableRows
            ):
                Failures.append(
                    "semantic truth-table row count mismatch: "
                    f"{TruthTableSemantics.get('RowCount')!r} != "
                    f"{Case.TruthTableRows}"
                )
            if TruthTableSemantics.get("AllRowsPassed") is not True:
                Failures.append("semantic truth table contains failed rows")
            if (
                TruthTableSemantics.get("ExpectedMatchesSimulated")
                is not True
            ):
                Failures.append(
                    "semantic expected/simulated truth-table digests differ"
                )
            for Name in (
                "ArithmeticResultSha256",
                "SimulationResultSha256",
            ):
                if not isinstance(TruthTableSemantics.get(Name), str):
                    Failures.append(
                        f"semantic truth table is missing {Name}"
                    )
            ExpectedArithmeticDigest = CanonicalArithmeticDigests.get(
                Case.Name
            )
            if ExpectedArithmeticDigest is None:
                Failures.append(
                    f"no canonical arithmetic digest for {Case.Name}"
                )
            elif (
                TruthTableSemantics.get("ArithmeticResultSha256")
                != ExpectedArithmeticDigest
            ):
                Failures.append(
                    "canonical arithmetic digest mismatch: "
                    f"{TruthTableSemantics.get('ArithmeticResultSha256')!r} "
                    f"!= {ExpectedArithmeticDigest}"
                )
        except (OSError, UnicodeError, ValueError) as Error:
            Failures.append(f"could not parse truth-table semantics: {Error}")

    PhysicalDocument: dict[str, object] | None = None
    if Artifacts["PhysicalDesign"].is_file():
        try:
            Loaded = json.loads(
                Artifacts["PhysicalDesign"].read_text(encoding="utf-8")
            )
            if not isinstance(Loaded, dict):
                raise ValueError("physical design root is not an object")
            PhysicalDocument = Loaded
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as Error:
            Failures.append(f"invalid PhysicalDesign JSON: {Error}")

    Evidence: dict[str, object] | None = None
    RouterReliability: dict[str, object] = {}
    Observed: dict[str, object] = {}
    SimulationBackend: str | None = None
    CandidateFingerprint: str | None = None
    ResourceGraphFingerprint: str | None = None
    EffectiveWorkFingerprint: str | None = None
    if PhysicalDocument is not None:
        Strategy = ReadNested(PhysicalDocument, "Strategy")
        if not isinstance(Strategy, dict):
            Failures.append("missing Strategy evidence")
            Strategy = {}
        RequestedStrategy = Strategy.get("Requested")
        UsedStrategy = Strategy.get("Used")
        if RequestedStrategy != "default":
            Failures.append("requested strategy is not default")
        if UsedStrategy != "default":
            Failures.append("used strategy is not default")
        if Strategy.get("FallbackUsed") is not False:
            Failures.append(
                "fallback was used or not explicitly disabled"
            )

        RouterReliability = ReadNested(PhysicalDocument, "RouterReliability")
        if not isinstance(RouterReliability, dict):
            Failures.append("missing RouterReliability evidence")
            RouterReliability = {}
        if RouterReliability.get("RunVerdict") != "ROUTED_AND_SIMULATED":
            Failures.append("successful router reliability verdict is missing")

        PolicySeed = ReadNested(PhysicalDocument, "Policy", "Seed")
        PolicyVersion = ReadNested(
            PhysicalDocument,
            "Policy",
            "PolicyVersion",
        )
        if PolicyVersion != ExpectedPolicyVersion:
            Failures.append(
                f"policy version is not {ExpectedPolicyVersion}"
            )
        if PolicySeed != ExpectedSeed:
            Failures.append(
                f"policy seed mismatch: {PolicySeed!r} != {ExpectedSeed!r}"
            )

        RunSummary = ReadNested(PhysicalDocument, "RunSummary")
        if not isinstance(RunSummary, dict):
            Failures.append("missing RunSummary evidence")
            RunSummary = {}
        PhysicalRuntime = RunSummary.get("RuntimeSeconds")
        if not isinstance(PhysicalRuntime, (int, float)) or isinstance(
            PhysicalRuntime, bool
        ):
            Failures.append("missing numeric RunSummary.RuntimeSeconds")
        elif float(PhysicalRuntime) > Case.RuntimeCeilingSeconds:
            Failures.append(
                "reported runtime exceeded ceiling: "
                f"{float(PhysicalRuntime):.6f}s > "
                f"{Case.RuntimeCeilingSeconds:.6f}s"
            )
        if RunSummary.get("TruthTablePassed") is not True:
            Failures.append("truth table did not pass")
        if RunSummary.get("TruthTableRows") != Case.TruthTableRows:
            Failures.append(
                "truth-table row count mismatch: "
                f"{RunSummary.get('TruthTableRows')!r} != {Case.TruthTableRows}"
            )
        RawSimulationBackend = RunSummary.get("SimulationBackend")
        if RawSimulationBackend not in {
            "python",
            "native-parallel",
        }:
            Failures.append("simulation backend is missing or non-authoritative")
        else:
            SimulationBackend = str(RawSimulationBackend)
        if RunSummary.get("Conflicts") != 0:
            Failures.append("RunSummary.Conflicts is not zero")
        OverflowPeak = RunSummary.get("OverflowPeak")
        if not isinstance(OverflowPeak, int) or isinstance(OverflowPeak, bool):
            Failures.append("missing integer RunSummary.OverflowPeak")
        elif OverflowPeak > Case.MaximumOverflowPeak:
            Failures.append(
                f"overflow peak exceeded {Case.MaximumOverflowPeak}: {OverflowPeak}"
            )

        FinalValidation = ReadNested(PhysicalDocument, "FinalValidation")
        if not isinstance(FinalValidation, dict):
            Failures.append("missing FinalValidation evidence")
            FinalValidation = {}
        if FinalValidation.get("ValidationMode") != "authoritative-exact":
            Failures.append("final validation mode is not authoritative-exact")
        if FinalValidation.get("ZeroConflicts") is not True:
            Failures.append("FinalValidation.ZeroConflicts is not true")
        if FinalValidation.get("ConflictCount") != 0:
            Failures.append("FinalValidation.ConflictCount is not zero")
        if FinalValidation.get("UnresolvedClaimCount") != 0:
            Failures.append("unresolved claim count is not zero")
        if FinalValidation.get("UnresolvedClaims") != []:
            Failures.append("unresolved claim list is not empty")

        Fingerprints = RouterReliability.get("Fingerprints")
        if not isinstance(Fingerprints, dict):
            Failures.append("missing router fingerprint evidence")
            Fingerprints = {}
        PlacementFingerprint = Fingerprints.get("Placement")
        if not isinstance(PlacementFingerprint, str) or not PlacementFingerprint:
            Failures.append("missing placement fingerprint")
        CandidateValue = Fingerprints.get("Candidate")
        if not isinstance(CandidateValue, str) or not CandidateValue:
            Failures.append("missing candidate fingerprint")
        else:
            CandidateFingerprint = CandidateValue
        ResourceGraphValue = Fingerprints.get("ResourceGraph")
        if (
            not isinstance(ResourceGraphValue, str)
            or not ResourceGraphValue
        ):
            Failures.append("missing resource-graph fingerprint")
        else:
            ResourceGraphFingerprint = ResourceGraphValue
        EffectiveWorkValue = Fingerprints.get("EffectiveWork")
        if EffectiveWorkValue is not None and (
            not isinstance(EffectiveWorkValue, str)
            or not EffectiveWorkValue
        ):
            Failures.append("invalid effective-work fingerprint")
        elif isinstance(EffectiveWorkValue, str):
            EffectiveWorkFingerprint = EffectiveWorkValue

        Ownership = ReadNested(
            PhysicalDocument,
            "RoutingResourceGraph",
            "OwnershipCounts",
        )
        if not isinstance(Ownership, dict):
            Failures.append("missing resource ownership counts")

        MissingRouteMetrics = [
            Name for Name in RouteMetricFields if Name not in RunSummary
        ]
        if MissingRouteMetrics:
            Failures.append(
                "missing route metrics: " + ", ".join(MissingRouteMetrics)
            )
        RouteMetrics = {
            Name: RunSummary.get(Name) for Name in RouteMetricFields
        }
        MissingFootprintMetrics = [
            Name
            for Name in (*FootprintMetricFields, *DimensionMetricFields)
            if (
                not isinstance(RunSummary.get(Name), int)
                or isinstance(RunSummary.get(Name), bool)
                or int(RunSummary.get(Name)) <= 0
            )
        ]
        if MissingFootprintMetrics:
            Failures.append(
                "missing positive footprint metrics: "
                + ", ".join(MissingFootprintMetrics)
            )
        FootprintMetrics = {
            Name: RunSummary.get(Name)
            for Name in (*DimensionMetricFields, *FootprintMetricFields)
        }
        if not MissingFootprintMetrics:
            Width = int(FootprintMetrics["Width"])
            Height = int(FootprintMetrics["Height"])
            Depth = int(FootprintMetrics["Depth"])
            if FootprintMetrics["Footprint"] != Width * Depth:
                Failures.append(
                    "Footprint is not Width * Depth"
                )
            if (
                FootprintMetrics["FullFootprint"]
                != Width * Height * Depth
            ):
                Failures.append(
                    "FullFootprint is not Width * Height * Depth"
                )

        DesignDigest = None
        LitematicComposition: dict[str, int] | None = None
        if Artifacts["Schematic"].is_file():
            try:
                DesignDigest = DesignDigestBuilder(Artifacts["Schematic"])
            except Exception as Error:
                Failures.append(f"could not digest emitted design: {Error}")
            try:
                LitematicComposition = (
                    LitematicCompositionEvidenceBuilder(
                        Artifacts["Schematic"]
                    )
                )
                if not isinstance(LitematicComposition, dict):
                    raise ValueError(
                        "litematic composition builder returned a non-object"
                    )
                for Name in (
                    *DimensionMetricFields,
                    *FootprintMetricFields,
                ):
                    SummaryValue = FootprintMetrics.get(Name)
                    EmittedValue = LitematicComposition.get(Name)
                    if SummaryValue != EmittedValue:
                        Failures.append(
                            "emitted litematic composition mismatch: "
                            f"{Name} {EmittedValue!r} != "
                            f"RunSummary {SummaryValue!r}"
                        )
            except Exception as Error:
                Failures.append(
                    f"could not measure emitted litematic composition: {Error}"
                )

        Observed = {
            "ReportedRuntimeSeconds": PhysicalRuntime,
            "TruthTablePassed": RunSummary.get("TruthTablePassed"),
            "TruthTableRows": RunSummary.get("TruthTableRows"),
            "Conflicts": RunSummary.get("Conflicts"),
            "UnresolvedClaimCount": FinalValidation.get(
                "UnresolvedClaimCount"
            ),
            "OverflowPeak": OverflowPeak,
            "PolicySeed": PolicySeed,
            "PolicyVersion": PolicyVersion,
            "SimulationBackend": SimulationBackend,
            "Fingerprints": {
                "Placement": PlacementFingerprint,
                "Candidate": CandidateFingerprint,
                "ResourceGraph": ResourceGraphFingerprint,
                "EffectiveWork": EffectiveWorkFingerprint,
            },
            "TruthTableSemantics": TruthTableSemantics,
            "FootprintMetrics": FootprintMetrics,
            "LitematicComposition": LitematicComposition,
        }
        if (
            isinstance(PlacementFingerprint, str)
            and PlacementFingerprint
            and isinstance(Ownership, dict)
            and not MissingRouteMetrics
            and not MissingFootprintMetrics
            and isinstance(DesignDigest, str)
            and DesignDigest
            and isinstance(CandidateFingerprint, str)
            and CandidateFingerprint
            and isinstance(ResourceGraphFingerprint, str)
            and ResourceGraphFingerprint
            and isinstance(SimulationBackend, str)
            and SimulationBackend
            and isinstance(LitematicComposition, dict)
            and isinstance(TruthTableSemantics, dict)
            and isinstance(
                TruthTableSemantics.get("ArithmeticResultSha256"),
                str,
            )
            and isinstance(
                TruthTableSemantics.get("SimulationResultSha256"),
                str,
            )
        ):
            StableArtifactSha256 = {
                "TruthTable": BuildFileRecord(
                    Artifacts["TruthTable"]
                ).get("Sha256"),
                "EmittedDesignSemantic": DesignDigest,
            }
            Evidence = {
                "PlacementFingerprint": PlacementFingerprint,
                "CandidateFingerprint": CandidateFingerprint,
                "ResourceGraphFingerprint": ResourceGraphFingerprint,
                "EffectiveWorkFingerprint": EffectiveWorkFingerprint,
                "OwnershipCounts": Ownership,
                "RouteMetrics": RouteMetrics,
                "FootprintMetrics": FootprintMetrics,
                "LitematicComposition": LitematicComposition,
                "SimulationBackend": SimulationBackend,
                "StableArtifactSha256": StableArtifactSha256,
                "EmittedDesignSha256": DesignDigest,
                "TruthTableArithmeticSha256": TruthTableSemantics[
                    "ArithmeticResultSha256"
                ],
                "TruthTableSimulationSha256": TruthTableSemantics[
                    "SimulationResultSha256"
                ],
            }

    Perf = BuildPerfTelemetry(
        RouterReliability if isinstance(RouterReliability, dict) else None
    )

    ArtifactRecords = {
        Name: BuildFileRecord(PathValue)
        for Name, PathValue in Artifacts.items()
        if Name != "RunDirectory"
    }
    Result = {
        "Accepted": not Failures,
        "Failures": Failures,
        "Process": {
            "ReturnCode": Process.ReturnCode,
            "TimedOut": Process.TimedOut,
            # Preserve the measured value for all gates. The rounded field is
            # presentation-only so a value microscopically above a hard
            # boundary cannot round down into a pass.
            "WallRuntimeSecondsRaw": float(Process.RuntimeSeconds),
            "WallRuntimeSeconds": round(Process.RuntimeSeconds, 6),
            "RuntimeCeilingSeconds": Case.RuntimeCeilingSeconds,
            "RequestedRoutingDeadlineSeconds": (
                Case.RoutingDeadlineSeconds
            ),
            "PublicationReserveSeconds": Case.PublicationReserveSeconds,
            "ProcessEnvelopeSeconds": ProcessEnvelopeSeconds,
            "ProcessEnvelopeValid": ProcessEnvelopeValid,
            "DeadlineOverrunSeconds": round(DeadlineOverrunSeconds, 6),
            "MaximumDeadlineOverrunSeconds": MaximumDeadlineOverrunSeconds,
            "DeadlineOverrunWithinLimit": DeadlineOverrunWithinLimit,
        },
        "Observed": Observed,
        "Perf": Perf,
        "Artifacts": ArtifactRecords,
    }
    return Result, Evidence


def ClearPriorRunArtifacts(Artifacts: dict[str, Path]) -> None:
    """Remove only exact prior outputs that could masquerade as this run."""
    for Name in (
        "Schematic",
        "TruthTable",
        "PhysicalDesign",
        "RoutingFailure",
        "Diagram",
        "Dot",
    ):
        Artifacts[Name].unlink(missing_ok=True)


def EncodeManifest(Manifest: dict[str, object]) -> bytes:
    """Encode a manifest using the one canonical on-disk representation."""
    return (
        json.dumps(Manifest, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def WriteManifest(PathValue: Path, Manifest: dict[str, object]) -> None:
    PathValue.parent.mkdir(parents=True, exist_ok=True)
    TemporaryPath = PathValue.with_suffix(".json.tmp")
    TemporaryPath.write_bytes(EncodeManifest(Manifest))
    TemporaryPath.replace(PathValue)


def _BuildPlannedCases(
    Configuration: AcceptanceConfiguration,
) -> tuple[AcceptanceCase, ...]:
    """Return the ordered case list that this run should execute."""
    IncludeExtendedCases = (
        Configuration.IncludeCla4
        and Configuration.BaselineMode != "capture"
    )
    return tuple(
        Case
        for Case in AcceptanceCases
        if (
            Case.Name in RegressionCaseNames
            or (IncludeExtendedCases and Case.Name in ExtendedCaseNames)
        )
    )


def BuildPlannedRuns(
    Configuration: AcceptanceConfiguration,
) -> list[dict[str, object]]:
    Runs = []
    Sequence = 0
    for Case in _BuildPlannedCases(Configuration):
        Repetitions: list[tuple[int, bool]] = []
        if (
            Configuration.BaselineMode in {"capture", "compare"}
            and Case.Name == "FullAdder"
        ):
            Repetitions.append((0, True))
        Repetitions.extend(
            (RunIndex, False)
            for RunIndex in range(1, Case.RequiredRuns + 1)
        )
        for RunIndex, IsWarmup in Repetitions:
            Sequence += 1
            RunName = (
                f"{Case.Name}Warmup"
                if IsWarmup
                else f"{Case.Name}Run{RunIndex}"
            )
            RunDirectory = Configuration.RecoveryRoot / RunName
            Artifacts = BuildRunArtifacts(RunDirectory, RunName)
            Command = BuildCompilerCommand(
                Configuration,
                Case,
                RunName,
                Artifacts,
            )
            Runs.append({
                "Sequence": Sequence,
                "RunName": RunName,
                "Circuit": Case.Name,
                "Repetition": RunIndex,
                "Warmup": IsWarmup,
                "MeasurementIncluded": not IsWarmup,
                "Requirements": Case.ToDictionary(),
                "RequestedRoutingDeadlineSeconds": (
                    Case.RoutingDeadlineSeconds
                ),
                "PublicationReserveSeconds": (
                    Case.PublicationReserveSeconds
                ),
                "Command": Command,
                "CommandText": shlex.join(Command),
                "ArtifactPaths": {
                    Name: str(Value.resolve(strict=False))
                    for Name, Value in Artifacts.items()
                },
                "Status": "PLANNED",
                "Accepted": False,
            })
    return Runs


def BuildSubprocessTimeoutSeconds(
    Case: AcceptanceCase,
    Configuration: AcceptanceConfiguration,
) -> float:
    """Allow deterministic post-deadline artifact publication.

    The wall ceiling remains the acceptance criterion.  This timeout only
    prevents a harness/process race at the exact moment the compiler returns
    its already-classified deadline result.
    """
    CaptureGrace = (
        Configuration.CaptureTimeoutGraceSeconds
        if Configuration.BaselineMode == "capture"
        else 0.0
    )
    return (
        Case.RuntimeCeilingSeconds
        + SubprocessFinalizationGraceSeconds
        + CaptureGrace
    )


RequiredCompatibilityFields = (
    "BenchmarkInputs",
    "BenchmarkProfile",
    "Platform",
    "CpuProfile",
    "PythonProfileSource",
    "PythonVersion",
    "PythonImplementation",
    "PythonExecutable",
    "PythonPrefix",
    "PythonBasePrefix",
    "RequestedPythonExecutable",
    "PolicySeed",
    "RoutingThreads",
    "RoutingEnvironment",
)
LegacyAdditiveCompatibilityFields = (
    "PhysicalTemplates",
    "CpuExecutionProfile",
    "LoadProfile",
)


def BuildComparisonCompatibility(
    *,
    Environment: dict[str, object],
    SourceProvenance: dict[str, object],
) -> dict[str, object]:
    """Select identity fields that must match across benchmark sessions."""
    BenchmarkInputs = SourceProvenance.get("BenchmarkInputs", {})
    if not isinstance(BenchmarkInputs, dict):
        BenchmarkInputs = {}
    RequiredInputs = {
        Case.Name: BenchmarkInputs.get(Case.Name)
        for Case in AcceptanceCases
    }
    BenchmarkProfile = {
        "SchemaVersion": "router-regression-profile-v1",
        "Cases": {
            Case.Name: {
                Key: Value
                for Key, Value in Case.ToDictionary().items()
                if Key != "NeedsExactInterfaceProof"
            }
            for Case in AcceptanceCases
        },
        "RegressionCircuits": sorted(RegressionCaseNames),
        "Warmup": {
            "Circuit": "FullAdder",
            "Count": 1,
            "MeasurementIncluded": False,
        },
        "RoutingStrategy": "default",
        "RoutingThreads": RequiredRegressionRoutingThreads,
        "PolicySeed": 0,
        "CanonicalArithmeticDigests": dict(
            sorted(CanonicalArithmeticDigests.items())
        ),
    }
    Result = {
        "BenchmarkInputs": RequiredInputs,
        "BenchmarkProfile": BenchmarkProfile,
        "Platform": Environment.get("Platform"),
        "CpuProfile": Environment.get("CpuProfile"),
        "PythonProfileSource": Environment.get("ProfileSource"),
        "PythonVersion": Environment.get("PythonVersion"),
        "PythonImplementation": Environment.get("PythonImplementation"),
        "PythonExecutable": Environment.get("PythonExecutable"),
        "PythonPrefix": Environment.get("PythonPrefix"),
        "PythonBasePrefix": Environment.get("PythonBasePrefix"),
        "RequestedPythonExecutable": Environment.get(
            "RequestedPythonExecutable"
        ),
        "PolicySeed": Environment.get("PolicySeed"),
        "RoutingThreads": Environment.get("RoutingThreads"),
        "RoutingEnvironment": Environment.get("RoutingEnvironment"),
    }
    if "PhysicalTemplates" in SourceProvenance:
        Result["PhysicalTemplates"] = SourceProvenance.get(
            "PhysicalTemplates"
        )
    if "CpuExecutionProfile" in Environment:
        Result["CpuExecutionProfile"] = Environment.get(
            "CpuExecutionProfile"
        )
    LoadProfile = Environment.get("LoadProfile")
    if isinstance(LoadProfile, dict):
        Result["LoadProfile"] = {
            "EffectiveCpuCapacity": LoadProfile.get(
                "EffectiveCpuCapacity"
            ),
            "CompatibilityClass": LoadProfile.get(
                "CompatibilityClass"
            ),
        }
    return Result


def CalculateRuntimeStatistics(
    Values: list[float],
) -> dict[str, object]:
    """Return median and worst relative deviation for a benchmark sample."""
    if not Values:
        return {
            "SamplesSecondsRaw": [],
            "SamplesSeconds": [],
            "MedianSecondsRaw": None,
            "MedianSeconds": None,
            "MaximumSpreadFractionRaw": None,
            "MaximumSpreadFraction": None,
            "Stable": False,
        }
    MedianSeconds = float(median(Values))
    SpreadFraction = (
        max(abs(Value - MedianSeconds) for Value in Values)
        / MedianSeconds
        if MedianSeconds > 0.0
        else float("inf")
    )
    Stable = (
        isfinite(MedianSeconds)
        and MedianSeconds > 0.0
        and all(
            isfinite(Value)
            and Value > 0.0
            and (
                MedianSeconds
                * (1.0 - MaximumRuntimeSpreadFraction)
                <= Value
                <= MedianSeconds
                * (1.0 + MaximumRuntimeSpreadFraction)
            )
            for Value in Values
        )
    )
    return {
        "SamplesSecondsRaw": [float(Value) for Value in Values],
        "SamplesSeconds": [round(Value, 6) for Value in Values],
        "MedianSecondsRaw": MedianSeconds,
        "MedianSeconds": round(MedianSeconds, 6),
        "MaximumSpreadFractionRaw": SpreadFraction,
        "MaximumSpreadFraction": round(SpreadFraction, 9),
        "Stable": Stable,
    }


def CalculateRecordedRuntimeBounds(
    Values: list[float],
    PrecisionSeconds: float,
) -> dict[str, object]:
    """Conservatively bound runtimes recorded at a declared precision."""
    HalfQuantum = PrecisionSeconds / 2.0
    LowerSamples = [Value - HalfQuantum for Value in Values]
    UpperSamples = [Value + HalfQuantum for Value in Values]
    MedianLowerBound = float(median(LowerSamples))
    MedianUpperBound = float(median(UpperSamples))
    MaximumSpreadFraction = (
        max(
            max(
                abs(Lower - MedianUpperBound),
                abs(Upper - MedianLowerBound),
            )
            for Lower, Upper in zip(LowerSamples, UpperSamples)
        )
        / MedianLowerBound
        if MedianLowerBound > 0.0
        else float("inf")
    )
    return {
        "ReferenceMedianLowerBoundSeconds": MedianLowerBound,
        "ReferenceMedianUpperBoundSeconds": MedianUpperBound,
        "MaximumSpreadFractionConservative": MaximumSpreadFraction,
        "StableConservative": (
            isfinite(MaximumSpreadFraction)
            and MaximumSpreadFraction <= MaximumRuntimeSpreadFraction
        ),
    }


def BuildCaseBaselineSummary(
    Case: AcceptanceCase,
    Runs: list[dict[str, object]],
) -> dict[str, object]:
    """Compact repeated run evidence into one promotable circuit record."""
    MeasuredRuns = [
        Run
        for Run in Runs
        if (
            Run.get("Circuit") == Case.Name
            and Run.get("MeasurementIncluded") is True
            and isinstance(Run.get("Evaluation"), dict)
        )
    ]

    def ReadRunWallRuntime(Run: dict[str, object]) -> float | None:
        RawValue = ReadNested(
            Run,
            "Evaluation",
            "Process",
            "WallRuntimeSecondsRaw",
        )
        Value = (
            RawValue
            if RawValue is not None
            else ReadNested(
                Run,
                "Evaluation",
                "Process",
                "WallRuntimeSeconds",
            )
        )
        if not isinstance(Value, (int, float)) or isinstance(Value, bool):
            return None
        return float(Value)

    MeasuredRuntimes = [
        ReadRunWallRuntime(Run)
        for Run in MeasuredRuns
    ]
    Runtimes = [
        Value for Value in MeasuredRuntimes if Value is not None
    ]
    Evidences = [
        ReadNested(Run, "Determinism", "Evidence")
        for Run in MeasuredRuns
    ]
    CompleteEvidences = [
        Evidence for Evidence in Evidences if isinstance(Evidence, dict)
    ]
    ReferenceEvidence = (
        CompleteEvidences[0] if CompleteEvidences else {}
    )
    Deterministic = (
        len(CompleteEvidences) == Case.RequiredRuns
        and all(
            all(
                Evidence.get(Name) == ReferenceEvidence.get(Name)
                for Name in DeterministicEvidenceFields
            )
            for Evidence in CompleteEvidences
        )
    )
    Runtime = CalculateRuntimeStatistics(Runtimes)
    Failures: list[str] = []
    Complete = len(MeasuredRuns) == Case.RequiredRuns
    AllRunsAccepted = (
        Complete
        and all(bool(Run.get("Accepted")) for Run in MeasuredRuns)
    )
    AccuracyAndDeterminismPassed = (
        AllRunsAccepted and Deterministic
    )
    if not Complete:
        Failures.append(
            f"expected {Case.RequiredRuns} measured runs; "
            f"found {len(MeasuredRuns)}"
        )
    if not AllRunsAccepted:
        Failures.append("one or more measured runs failed")
    if not Deterministic:
        Failures.append("repeated physical/truth evidence is not deterministic")
    if Runtime["Stable"] is not True:
        Failures.append(
            "runtime spread exceeded "
            f"{MaximumRuntimeSpreadFraction:.0%}"
        )
    return {
        "Requirements": {
            Key: Value
            for Key, Value in Case.ToDictionary().items()
            if Key != "NeedsExactInterfaceProof"
        },
        "MeasuredRunCount": len(MeasuredRuns),
        "RunNames": [Run.get("RunName") for Run in MeasuredRuns],
        "Runtime": Runtime,
        "FootprintMetrics": ReferenceEvidence.get("FootprintMetrics"),
        "LitematicComposition": ReferenceEvidence.get(
            "LitematicComposition"
        ),
        "Dimensions": {
            Name: ReadNested(
                ReferenceEvidence,
                "FootprintMetrics",
                Name,
            )
            for Name in DimensionMetricFields
        },
        "RouteMetrics": ReferenceEvidence.get("RouteMetrics"),
        "PlacementFingerprint": ReferenceEvidence.get(
            "PlacementFingerprint"
        ),
        "CandidateFingerprint": ReferenceEvidence.get(
            "CandidateFingerprint"
        ),
        "ResourceGraphFingerprint": ReferenceEvidence.get(
            "ResourceGraphFingerprint"
        ),
        "EffectiveWorkFingerprint": ReferenceEvidence.get(
            "EffectiveWorkFingerprint"
        ),
        "OwnershipCounts": ReferenceEvidence.get("OwnershipCounts"),
        "SimulationBackend": ReferenceEvidence.get("SimulationBackend"),
        "StableArtifactSha256": ReferenceEvidence.get(
            "StableArtifactSha256"
        ),
        "EmittedDesignSha256": ReferenceEvidence.get(
            "EmittedDesignSha256"
        ),
        "TruthTableArithmeticSha256": ReferenceEvidence.get(
            "TruthTableArithmeticSha256"
        ),
        "TruthTableSimulationSha256": ReferenceEvidence.get(
            "TruthTableSimulationSha256"
        ),
        "Deterministic": Deterministic,
        "AccuracyAndDeterminismPassed": (
            AccuracyAndDeterminismPassed
        ),
        "Promotable": not Failures,
        "Failures": Failures,
    }


def BuildBaselineReference(
    Manifest: dict[str, object],
) -> dict[str, object]:
    """Build the compact reference written by a successful capture."""
    Runs = Manifest.get("Runs", [])
    if not isinstance(Runs, list):
        Runs = []
    Cases = {
        Case.Name: BuildCaseBaselineSummary(Case, Runs)
        for Case in AcceptanceCases
        if Case.Name in RegressionCaseNames
    }
    Warmups = [
        Run
        for Run in Runs
        if Run.get("Warmup") is True
    ]
    WarmupsPassed = (
        len(Warmups) == 1
        and all(bool(Run.get("Accepted")) for Run in Warmups)
    )
    Promotable = (
        WarmupsPassed
        and Manifest.get("SourceProvenanceStable") is True
        and all(
            bool(CaseSummary.get("Promotable"))
            for CaseSummary in Cases.values()
        )
    )
    return {
        "SchemaVersion": BaselineSchemaVersion,
        "CapturedAtUtc": Manifest.get("CompletedAtUtc"),
        "Promotable": Promotable,
        "WarmupsPassed": WarmupsPassed,
        "SourceProvenance": Manifest.get("SourceProvenance"),
        "ProvenanceChecks": Manifest.get("ProvenanceChecks"),
        "Environment": Manifest.get("Environment"),
        "Compatibility": Manifest.get("ComparisonCompatibility"),
        "Cases": Cases,
    }


def VerifyRecordedRuntimeSourceManifest(
    BaselinePath: Path,
    RecordedRuntimeEvidence: dict[str, object],
) -> bool:
    """Verify a referenced raw manifest when that ignored artifact is local."""
    RawPath = RecordedRuntimeEvidence.get("SourceManifestPath")
    ExpectedSha256 = RecordedRuntimeEvidence.get("SourceManifestSha256")
    if (
        not isinstance(RawPath, str)
        or not RawPath
        or not isinstance(ExpectedSha256, str)
        or len(ExpectedSha256) != 64
    ):
        return False
    SourcePath = Path(RawPath)
    CandidatePaths = (
        [SourcePath]
        if SourcePath.is_absolute()
        else [
            RepositoryRoot / SourcePath,
            BaselinePath.parent / SourcePath,
        ]
    )
    Seen: set[Path] = set()
    for CandidatePath in CandidatePaths:
        ResolvedPath = CandidatePath.resolve(strict=False)
        if ResolvedPath in Seen:
            continue
        Seen.add(ResolvedPath)
        if not ResolvedPath.is_file():
            continue
        ObservedSha256 = sha256(ResolvedPath.read_bytes()).hexdigest()
        if ObservedSha256 != ExpectedSha256:
            raise ValueError(
                "recorded-runtime source manifest hash mismatch: "
                f"{ObservedSha256} != {ExpectedSha256}"
            )
        return True
    # Compact references remain portable; ignored raw Output is corroborating
    # evidence when present, not a required distribution dependency.
    return False


def ValidateFirstValidCaseSummary(
    Case: AcceptanceCase,
    Summary: object,
) -> None:
    """Validate one independently promoted circuit baseline summary."""
    if not isinstance(Summary, dict):
        raise ValueError(
            f"first-valid baseline case {Case.Name} is not an object"
        )
    for Name in (
        "Promotable",
        "AccuracyAndDeterminismPassed",
        "Deterministic",
    ):
        if Summary.get(Name) is not True:
            raise ValueError(
                f"first-valid baseline case {Case.Name} has invalid {Name}"
            )
    if Summary.get("Failures") != []:
        raise ValueError(
            f"first-valid baseline case {Case.Name} contains failures"
        )
    if Summary.get("MeasuredRunCount") != Case.RequiredRuns:
        raise ValueError(
            f"first-valid baseline case {Case.Name} measured-run count is "
            f"not {Case.RequiredRuns}"
        )
    ExpectedRunNames = [
        f"{Case.Name}Run{Index}"
        for Index in range(1, Case.RequiredRuns + 1)
    ]
    if Summary.get("RunNames") != ExpectedRunNames:
        raise ValueError(
            f"first-valid baseline case {Case.Name} run-name evidence is "
            "incomplete"
        )
    if Summary.get("Requirements") != {
        Key: Value
        for Key, Value in Case.ToDictionary().items()
        if Key != "NeedsExactInterfaceProof"
    }:
        raise ValueError(
            f"first-valid baseline case {Case.Name} requirements do not "
            "match the benchmark profile"
        )
    ExpectedTruthDigest = CanonicalArithmeticDigests[Case.Name]
    for Name in (
        "TruthTableArithmeticSha256",
        "TruthTableSimulationSha256",
    ):
        if Summary.get(Name) != ExpectedTruthDigest:
            raise ValueError(
                f"first-valid baseline case {Case.Name} has invalid {Name}"
            )
    for Name in (
        "PlacementFingerprint",
        "CandidateFingerprint",
        "ResourceGraphFingerprint",
        "EmittedDesignSha256",
    ):
        if not isinstance(Summary.get(Name), str) or not Summary[Name]:
            raise ValueError(
                f"first-valid baseline case {Case.Name} has no {Name}"
            )
    EffectiveWorkFingerprint = Summary.get("EffectiveWorkFingerprint")
    if EffectiveWorkFingerprint is not None and (
        not isinstance(EffectiveWorkFingerprint, str)
        or not EffectiveWorkFingerprint
    ):
        raise ValueError(
            f"first-valid baseline case {Case.Name} has invalid "
            "EffectiveWorkFingerprint"
        )
    if Summary.get("SimulationBackend") not in {
        "python",
        "native-parallel",
    }:
        raise ValueError(
            f"first-valid baseline case {Case.Name} has invalid "
            "SimulationBackend"
        )
    StableArtifactSha256 = Summary.get("StableArtifactSha256")
    if not isinstance(StableArtifactSha256, dict) or any(
        not isinstance(StableArtifactSha256.get(Name), str)
        or len(StableArtifactSha256[Name]) != 64
        for Name in ("TruthTable", "EmittedDesignSemantic")
    ):
        raise ValueError(
            f"first-valid baseline case {Case.Name} has invalid stable "
            "artifact hashes"
        )

    Footprint = Summary.get("FootprintMetrics")
    Dimensions = Summary.get("Dimensions")
    if not isinstance(Footprint, dict) or not isinstance(Dimensions, dict):
        raise ValueError(
            f"first-valid baseline case {Case.Name} has no footprint evidence"
        )
    RequiredFootprintNames = (
        *DimensionMetricFields,
        *FootprintMetricFields,
    )
    InvalidFootprints = [
        Name
        for Name in RequiredFootprintNames
        if (
            not isinstance(Footprint.get(Name), int)
            or isinstance(Footprint.get(Name), bool)
            or Footprint[Name] <= 0
        )
    ]
    if InvalidFootprints:
        raise ValueError(
            f"first-valid baseline case {Case.Name} has invalid footprint "
            "metrics: " + ", ".join(InvalidFootprints)
        )
    if any(
        Dimensions.get(Name) != Footprint[Name]
        for Name in DimensionMetricFields
    ):
        raise ValueError(
            f"first-valid baseline case {Case.Name} dimensions do not match "
            "footprint evidence"
        )
    Width = Footprint["Width"]
    Height = Footprint["Height"]
    Depth = Footprint["Depth"]
    if Footprint["Footprint"] != Width * Depth:
        raise ValueError(
            f"first-valid baseline case {Case.Name} Footprint is not "
            "Width * Depth"
        )
    if Footprint["FullFootprint"] != Width * Height * Depth:
        raise ValueError(
            f"first-valid baseline case {Case.Name} FullFootprint is not "
            "Width * Height * Depth"
        )
    if Footprint["ExactNonAirBlocks"] > Footprint["FullFootprint"]:
        raise ValueError(
            f"first-valid baseline case {Case.Name} exact block count "
            "exceeds the full footprint"
        )

    RouteMetrics = Summary.get("RouteMetrics")
    if not isinstance(RouteMetrics, dict):
        raise ValueError(
            f"first-valid baseline case {Case.Name} has no route metrics"
        )
    if RouteMetrics.get("Conflicts") != 0:
        raise ValueError(
            f"first-valid baseline case {Case.Name} contains conflicts"
        )
    OverflowPeak = RouteMetrics.get("OverflowPeak")
    if (
        not isinstance(OverflowPeak, int)
        or isinstance(OverflowPeak, bool)
        or OverflowPeak < 0
        or OverflowPeak > Case.MaximumOverflowPeak
    ):
        raise ValueError(
            f"first-valid baseline case {Case.Name} has invalid overflow"
        )

    Runtime = Summary.get("Runtime")
    if not isinstance(Runtime, dict):
        raise ValueError(
            f"first-valid baseline case {Case.Name} has no runtime evidence"
        )
    RawSamples = Runtime.get("SamplesSecondsRaw")
    if (
        not isinstance(RawSamples, list)
        or len(RawSamples) != Case.RequiredRuns
        or any(
            not isinstance(Value, (int, float))
            or isinstance(Value, bool)
            or not isfinite(float(Value))
            or float(Value) <= 0.0
            or float(Value) > Case.RuntimeCeilingSeconds
            for Value in RawSamples
        )
    ):
        raise ValueError(
            f"first-valid baseline case {Case.Name} has invalid raw runtime "
            "samples"
        )
    CalculatedRuntime = CalculateRuntimeStatistics([
        float(Value) for Value in RawSamples
    ])
    for Name in (
        "SamplesSecondsRaw",
        "SamplesSeconds",
        "MedianSecondsRaw",
        "MedianSeconds",
        "MaximumSpreadFractionRaw",
        "MaximumSpreadFraction",
        "Stable",
    ):
        if Runtime.get(Name) != CalculatedRuntime[Name]:
            raise ValueError(
                f"first-valid baseline case {Case.Name} runtime {Name} does "
                "not match raw samples"
            )
    if CalculatedRuntime["Stable"] is not True:
        raise ValueError(
            f"first-valid baseline case {Case.Name} runtime spread exceeds "
            f"{MaximumRuntimeSpreadFraction:.0%}"
        )


def ValidateFirstValidBaselines(
    Baseline: dict[str, object],
) -> None:
    """Validate the append-only first-valid circuit extension, if present."""
    FirstValidBaselines = Baseline.get("FirstValidBaselines")
    if FirstValidBaselines is None:
        return
    if not isinstance(FirstValidBaselines, dict):
        raise ValueError("baseline FirstValidBaselines is not an object")
    Unexpected = sorted(
        set(FirstValidBaselines).difference(ExtendedCaseNames)
    )
    if Unexpected:
        raise ValueError(
            "baseline has unexpected compatibility-first-valid circuits: "
            + ", ".join(Unexpected)
        )
    if not FirstValidBaselines:
        return

    for ExtendedName in ExtendedCaseNames:
        Entry = FirstValidBaselines.get(ExtendedName)
        if Entry is None:
            continue
        if not isinstance(Entry, dict):
            raise ValueError(
                f"{ExtendedName} first-valid baseline is not an object"
            )
        if Entry.get("SchemaVersion") != FirstValidBaselineSchemaVersion:
            raise ValueError(
                f"{ExtendedName} first-valid baseline has an "
                "invalid schema version"
            )
        if Entry.get("Circuit") != ExtendedName:
            raise ValueError(
                f"{ExtendedName} first-valid baseline has an invalid "
                "circuit"
            )
        PromotedAtUtc = Entry.get("PromotedAtUtc")
        if not isinstance(PromotedAtUtc, str) or not PromotedAtUtc:
            raise ValueError(
                f"{ExtendedName} first-valid baseline has no promotion "
                "timestamp"
            )
        OriginalReferenceSha256 = Entry.get("OriginalReferenceSha256")
        if (
            not isinstance(OriginalReferenceSha256, str)
            or len(OriginalReferenceSha256) != 64
        ):
            raise ValueError(
                f"{ExtendedName} first-valid baseline has no original "
                "reference SHA-256"
            )
        try:
            int(OriginalReferenceSha256, 16)
        except ValueError as Error:
            raise ValueError(
                f"{ExtendedName} first-valid baseline original "
                "reference SHA-256 is invalid"
            ) from Error
        OriginalReferenceContentSha256 = Entry.get(
            "OriginalReferenceContentSha256"
        )
        if (
            not isinstance(OriginalReferenceContentSha256, str)
            or len(OriginalReferenceContentSha256) != 64
        ):
            raise ValueError(
                f"{ExtendedName} first-valid baseline has no original "
                "reference content SHA-256"
            )
        try:
            int(OriginalReferenceContentSha256, 16)
        except ValueError as Error:
            raise ValueError(
                f"{ExtendedName} first-valid baseline original reference "
                "content SHA-256 is invalid"
            ) from Error
        OriginalFieldPresent = Entry.get(
            "OriginalFirstValidBaselinesFieldPresent"
        )
        if not isinstance(OriginalFieldPresent, bool):
            raise ValueError(
                f"{ExtendedName} first-valid baseline has invalid "
                "original extension state"
            )

        OriginalReference = deepcopy(Baseline)
        if OriginalFieldPresent:
            OriginalReference["FirstValidBaselines"] = {
                Name: Value
                for Name, Value in (
                    (Name, BaselineEntry)
                    for Name, BaselineEntry in FirstValidBaselines.items()
                    if Name in ExtendedCaseNames
                )
                if Name != ExtendedName
            }
        else:
            OriginalReference.pop("FirstValidBaselines", None)
        CalculatedOriginalSha256 = sha256(
            EncodeManifest(OriginalReference)
        ).hexdigest()
        if CalculatedOriginalSha256 != OriginalReferenceContentSha256:
            raise ValueError(
                f"{ExtendedName} first-valid baseline does not match "
                "its original reference content SHA-256"
            )

        SourceProvenance = Entry.get("SourceProvenance")
        Environment = Entry.get("Environment")
        Compatibility = Entry.get("Compatibility")
        if not isinstance(SourceProvenance, dict):
            raise ValueError(
                f"{ExtendedName} first-valid baseline has no "
                "SourceProvenance object"
            )
        if not isinstance(Environment, dict):
            raise ValueError(
                f"{ExtendedName} first-valid baseline has no Environment "
                "object"
            )
        if not isinstance(Compatibility, dict):
            raise ValueError(
                f"{ExtendedName} first-valid baseline has no "
                "Compatibility object"
            )
        if SourceProvenance.get(
            "ExpectedPolicyVersion"
        ) != CurrentPolicyVersion:
            raise ValueError(
                f"{ExtendedName} first-valid baseline has an "
                "unexpected policy version"
            )
        if Environment.get("PolicySeed") != 0 or Environment.get(
            "RoutingThreads"
        ) != RequiredRegressionRoutingThreads:
            raise ValueError(
                f"{ExtendedName} first-valid baseline has an "
                "invalid seed/thread profile"
            )
        ExpectedCompatibility = BuildComparisonCompatibility(
            Environment=Environment,
            SourceProvenance=SourceProvenance,
        )
        if Compatibility != ExpectedCompatibility:
            raise ValueError(
                f"{ExtendedName} first-valid Compatibility does not match "
                "Environment and SourceProvenance"
            )
        ProvenanceChecks = Entry.get("ProvenanceChecks")
        if (
            not isinstance(ProvenanceChecks, list)
            or len(ProvenanceChecks) < 2
            or any(
                not isinstance(Check, dict)
                or Check.get("Stable") is not True
                or Check.get("SourceProvenance") != SourceProvenance
                for Check in ProvenanceChecks
            )
        ):
            raise ValueError(
                f"{ExtendedName} first-valid source provenance checks are "
                "not stable"
            )
        Case = next(
            Value
            for Value in AcceptanceCases
            if Value.Name == ExtendedName
        )
        ValidateFirstValidCaseSummary(Case, Entry.get("CaseSummary"))


def ReadBaselineReference(PathValue: Path) -> dict[str, object]:
    """Read and strictly validate a compact baseline reference."""
    Loaded = json.loads(PathValue.read_text(encoding="utf-8"))
    if not isinstance(Loaded, dict):
        raise ValueError("baseline root is not an object")
    if Loaded.get("SchemaVersion") != BaselineSchemaVersion:
        raise ValueError(
            f"baseline schema is not {BaselineSchemaVersion}"
        )
    if Loaded.get("Promotable") is not True:
        raise ValueError("baseline is not marked promotable")
    if Loaded.get("WarmupsPassed") is not True:
        raise ValueError("baseline warm-up evidence did not pass")
    Cases = Loaded.get("Cases")
    if not isinstance(Cases, dict):
        raise ValueError("baseline has no Cases object")
    Missing = sorted(RegressionCaseNames.difference(Cases))
    if Missing:
        raise ValueError(
            "baseline is missing regression cases: " + ", ".join(Missing)
        )
    UnexpectedExtended = sorted(ExtendedCaseNames.intersection(Cases))
    if UnexpectedExtended:
        raise ValueError(
            "baseline must not include pre-change compatibility records: "
            + ", ".join(UnexpectedExtended)
        )
    Unexpected = sorted(set(Cases).difference(RegressionCaseNames))
    if Unexpected:
        raise ValueError(
            "baseline has unexpected regression cases: "
            + ", ".join(Unexpected)
        )

    SourceProvenance = Loaded.get("SourceProvenance")
    Environment = Loaded.get("Environment")
    StoredCompatibility = Loaded.get("Compatibility")
    if not isinstance(SourceProvenance, dict):
        raise ValueError("baseline has no SourceProvenance object")
    if not isinstance(Environment, dict):
        raise ValueError("baseline has no Environment object")
    if not isinstance(StoredCompatibility, dict):
        raise ValueError("baseline has no Compatibility object")
    ExpectedCompatibility = BuildComparisonCompatibility(
        Environment=Environment,
        SourceProvenance=SourceProvenance,
    )
    if StoredCompatibility != ExpectedCompatibility:
        raise ValueError(
            "baseline Compatibility does not match "
            "Environment and SourceProvenance"
        )
    ProvenanceChecks = Loaded.get("ProvenanceChecks")
    if (
        not isinstance(ProvenanceChecks, list)
        or not ProvenanceChecks
        or any(
            not isinstance(Check, dict)
            or Check.get("Stable") is not True
            or Check.get("SourceProvenance") != SourceProvenance
            for Check in ProvenanceChecks
        )
    ):
        raise ValueError("baseline source provenance checks are not stable")
    RecordedRuntimeEvidence = Loaded.get("RecordedRuntimeEvidence")
    ModernEvidenceRequired = "PhysicalTemplates" in SourceProvenance
    if isinstance(RecordedRuntimeEvidence, dict):
        VerifyRecordedRuntimeSourceManifest(
            PathValue,
            RecordedRuntimeEvidence,
        )

    for Case in AcceptanceCases:
        if Case.Name not in RegressionCaseNames:
            continue
        Summary = Cases[Case.Name]
        if not isinstance(Summary, dict):
            raise ValueError(
                f"baseline case {Case.Name} is not an object"
            )
        for Name in (
            "Promotable",
            "AccuracyAndDeterminismPassed",
            "Deterministic",
        ):
            if Summary.get(Name) is not True:
                raise ValueError(
                    f"baseline case {Case.Name} has invalid {Name}"
                )
        if Summary.get("Failures") != []:
            raise ValueError(
                f"baseline case {Case.Name} contains failures"
            )
        if Summary.get("MeasuredRunCount") != Case.RequiredRuns:
            raise ValueError(
                f"baseline case {Case.Name} measured-run count is not "
                f"{Case.RequiredRuns}"
            )
        ExpectedRunNames = [
            f"{Case.Name}Run{Index}"
            for Index in range(1, Case.RequiredRuns + 1)
        ]
        if Summary.get("RunNames") != ExpectedRunNames:
            raise ValueError(
                f"baseline case {Case.Name} run-name evidence is incomplete"
            )
            if Summary.get("Requirements") != {
                Key: Value
                for Key, Value in Case.ToDictionary().items()
                if Key != "NeedsExactInterfaceProof"
            }:
                raise ValueError(
                    f"baseline case {Case.Name} requirements do not match "
                    "the benchmark profile"
                )
        ExpectedTruthDigest = CanonicalArithmeticDigests[Case.Name]
        for Name in (
            "TruthTableArithmeticSha256",
            "TruthTableSimulationSha256",
        ):
            if Summary.get(Name) != ExpectedTruthDigest:
                raise ValueError(
                    f"baseline case {Case.Name} has invalid {Name}"
                )
        if not isinstance(Summary.get("PlacementFingerprint"), str) or not (
            Summary["PlacementFingerprint"]
        ):
            raise ValueError(
                f"baseline case {Case.Name} has no placement fingerprint"
            )
        if not isinstance(Summary.get("EmittedDesignSha256"), str) or not (
            Summary["EmittedDesignSha256"]
        ):
            raise ValueError(
                f"baseline case {Case.Name} has no emitted-design digest"
            )
        if ModernEvidenceRequired:
            for Name in (
                "CandidateFingerprint",
                "ResourceGraphFingerprint",
            ):
                if not isinstance(Summary.get(Name), str) or not Summary[Name]:
                    raise ValueError(
                        f"baseline case {Case.Name} has no {Name}"
                    )
            EffectiveWorkFingerprint = Summary.get(
                "EffectiveWorkFingerprint"
            )
            if EffectiveWorkFingerprint is not None and (
                not isinstance(EffectiveWorkFingerprint, str)
                or not EffectiveWorkFingerprint
            ):
                raise ValueError(
                    f"baseline case {Case.Name} has invalid "
                    "EffectiveWorkFingerprint"
                )
            if Summary.get("SimulationBackend") not in {
                "python",
                "native-parallel",
            }:
                raise ValueError(
                    f"baseline case {Case.Name} has invalid "
                    "SimulationBackend"
                )
            StableArtifactSha256 = Summary.get(
                "StableArtifactSha256"
            )
            if not isinstance(StableArtifactSha256, dict) or any(
                not isinstance(StableArtifactSha256.get(Name), str)
                or len(StableArtifactSha256[Name]) != 64
                for Name in ("TruthTable", "EmittedDesignSemantic")
            ):
                raise ValueError(
                    f"baseline case {Case.Name} has invalid stable "
                    "artifact hashes"
                )

        Footprint = Summary.get("FootprintMetrics")
        Dimensions = Summary.get("Dimensions")
        if not isinstance(Footprint, dict) or not isinstance(
            Dimensions,
            dict,
        ):
            raise ValueError(
                f"baseline case {Case.Name} has no footprint evidence"
            )
        RequiredFootprintNames = (
            *DimensionMetricFields,
            *FootprintMetricFields,
        )
        InvalidFootprints = [
            Name
            for Name in RequiredFootprintNames
            if (
                not isinstance(Footprint.get(Name), int)
                or isinstance(Footprint.get(Name), bool)
                or Footprint[Name] <= 0
            )
        ]
        if InvalidFootprints:
            raise ValueError(
                f"baseline case {Case.Name} has invalid footprint metrics: "
                + ", ".join(InvalidFootprints)
            )
        if any(
            Dimensions.get(Name) != Footprint[Name]
            for Name in DimensionMetricFields
        ):
            raise ValueError(
                f"baseline case {Case.Name} dimensions do not match "
                "footprint evidence"
            )
        Width = Footprint["Width"]
        Height = Footprint["Height"]
        Depth = Footprint["Depth"]
        if Footprint["Footprint"] != Width * Depth:
            raise ValueError(
                f"baseline case {Case.Name} Footprint is not Width * Depth"
            )
        if Footprint["FullFootprint"] != Width * Height * Depth:
            raise ValueError(
                f"baseline case {Case.Name} FullFootprint is not "
                "Width * Height * Depth"
            )
        if Footprint["ExactNonAirBlocks"] > Footprint["FullFootprint"]:
            raise ValueError(
                f"baseline case {Case.Name} exact block count exceeds "
                "the full footprint"
            )

        Runtime = Summary.get("Runtime")
        if not isinstance(Runtime, dict):
            raise ValueError(
                f"baseline case {Case.Name} has no runtime evidence"
            )
        RawSamples = Runtime.get("SamplesSecondsRaw")
        if isinstance(RawSamples, list):
            if (
                len(RawSamples) != Case.RequiredRuns
                or any(
                    not isinstance(Value, (int, float))
                    or isinstance(Value, bool)
                    or not isfinite(float(Value))
                    or float(Value) <= 0.0
                    for Value in RawSamples
                )
            ):
                raise ValueError(
                    f"baseline case {Case.Name} has invalid raw runtime samples"
                )
            CalculatedRuntime = CalculateRuntimeStatistics([
                float(Value) for Value in RawSamples
            ])
            for Name in (
                "SamplesSecondsRaw",
                "SamplesSeconds",
                "MedianSecondsRaw",
                "MedianSeconds",
                "MaximumSpreadFractionRaw",
                "MaximumSpreadFraction",
                "Stable",
            ):
                if Runtime.get(Name) != CalculatedRuntime[Name]:
                    raise ValueError(
                        f"baseline case {Case.Name} runtime {Name} does not "
                        "match raw samples"
                    )
            if CalculatedRuntime["Stable"] is not True:
                raise ValueError(
                    f"baseline case {Case.Name} runtime spread exceeds "
                    f"{MaximumRuntimeSpreadFraction:.0%}"
                )
        else:
            RecordedSamples = Runtime.get("SamplesSeconds")
            PrecisionSeconds = Runtime.get("RecordedPrecisionSeconds")
            if (
                not isinstance(RecordedSamples, list)
                or not isinstance(PrecisionSeconds, (int, float))
                or isinstance(PrecisionSeconds, bool)
                or not isfinite(float(PrecisionSeconds))
                or float(PrecisionSeconds) <= 0.0
            ):
                raise ValueError(
                    f"baseline case {Case.Name} lacks raw runtime samples; "
                    "rounded legacy references require declared precision "
                    "and conservative bounds"
                )
            if (
                not isinstance(RecordedRuntimeEvidence, dict)
                or not isinstance(
                    RecordedRuntimeEvidence.get("SourceManifestPath"),
                    str,
                )
                or not RecordedRuntimeEvidence["SourceManifestPath"]
                or not isinstance(
                    RecordedRuntimeEvidence.get("SourceManifestSha256"),
                    str,
                )
                or len(RecordedRuntimeEvidence["SourceManifestSha256"]) != 64
                or RecordedRuntimeEvidence.get(
                    "WallRuntimeRecordedPrecisionSeconds"
                )
                != PrecisionSeconds
            ):
                raise ValueError(
                    f"baseline case {Case.Name} lacks traceable "
                    "recorded-runtime provenance"
                )
            if (
                len(RecordedSamples) != Case.RequiredRuns
                or any(
                    not isinstance(Value, (int, float))
                    or isinstance(Value, bool)
                    or not isfinite(float(Value))
                    or float(Value) <= 0.0
                    for Value in RecordedSamples
                )
            ):
                raise ValueError(
                    f"baseline case {Case.Name} has invalid recorded "
                    "runtime samples"
                )
            RecordedValues = [float(Value) for Value in RecordedSamples]
            RecordedStatistics = CalculateRuntimeStatistics(RecordedValues)
            for Name in (
                "SamplesSeconds",
                "MedianSeconds",
                "MaximumSpreadFraction",
                "Stable",
            ):
                if Runtime.get(Name) != RecordedStatistics[Name]:
                    raise ValueError(
                        f"baseline case {Case.Name} runtime {Name} does not "
                        "match recorded samples"
                    )
            ConservativeBounds = CalculateRecordedRuntimeBounds(
                RecordedValues,
                float(PrecisionSeconds),
            )
            for Name, Value in ConservativeBounds.items():
                if Runtime.get(Name) != Value:
                    raise ValueError(
                        f"baseline case {Case.Name} runtime {Name} does not "
                        "match its declared recorded precision"
                    )
            if ConservativeBounds["StableConservative"] is not True:
                raise ValueError(
                    f"baseline case {Case.Name} conservative runtime spread "
                    f"exceeds {MaximumRuntimeSpreadFraction:.0%}"
                )
    ValidateFirstValidBaselines(Loaded)
    return Loaded


def BuildFirstValidBaselineEntry(
    *,
    Case: AcceptanceCase,
    CaseSummary: dict[str, object],
    SourceProvenance: dict[str, object],
    Environment: dict[str, object],
    Compatibility: dict[str, object],
    ProvenanceChecks: list[object],
    PromotedAtUtc: str,
    OriginalReferenceSha256: str,
    OriginalReferenceContentSha256: str,
    OriginalFirstValidBaselinesFieldPresent: bool,
) -> dict[str, object]:
    """Build the immutable record for a circuit with no pre-change baseline."""
    return {
        "SchemaVersion": FirstValidBaselineSchemaVersion,
        "Circuit": Case.Name,
        "PromotedAtUtc": PromotedAtUtc,
        "OriginalReferenceSha256": OriginalReferenceSha256,
        "OriginalReferenceContentSha256": (
            OriginalReferenceContentSha256
        ),
        "OriginalFirstValidBaselinesFieldPresent": (
            OriginalFirstValidBaselinesFieldPresent
        ),
        "CaseSummary": deepcopy(CaseSummary),
        "SourceProvenance": deepcopy(SourceProvenance),
        "Environment": deepcopy(Environment),
        "Compatibility": deepcopy(Compatibility),
        "ProvenanceChecks": deepcopy(ProvenanceChecks),
    }


def PromoteFirstValidBaseline(
    *,
    PathValue: Path,
    Baseline: dict[str, object],
    Entry: dict[str, object],
    CompatibilityCaseName: str,
    ExpectedOriginalSha256: str,
    ReferenceReader: Callable[[Path], dict[str, object]],
    ReferenceWriter: Callable[[Path, dict[str, object]], None],
) -> dict[str, object]:
    """Stage, validate, atomically publish, and reread one compatibility baseline."""
    Existing = Baseline.get("FirstValidBaselines")
    if Existing is not None and not isinstance(Existing, dict):
        raise ValueError("baseline FirstValidBaselines is not an object")
    if isinstance(Existing, dict) and Existing:
        raise ValueError(
            "compatibility-first-valid baseline already exists; overwrite refused"
        )
    OriginalBytes = PathValue.read_bytes()
    ObservedOriginalSha256 = sha256(OriginalBytes).hexdigest()
    if ObservedOriginalSha256 != ExpectedOriginalSha256:
        raise ValueError(
            f"{CompatibilityCaseName} baseline changed before promotion; "
            "overwrite refused"
        )
    CandidateReference = deepcopy(Baseline)
    CandidateReference["FirstValidBaselines"] = {
        CompatibilityCaseName: deepcopy(Entry)
    }
    StagingPath = PathValue.with_name(
        PathValue.name + ".compat-first-valid-promotion-stage"
    )
    WriterTemporaryPath = StagingPath.with_suffix(".json.tmp")
    RestorePath = PathValue.with_name(
        PathValue.name + ".compat-first-valid-promotion-restore"
    )
    if (
        StagingPath.exists()
        or WriterTemporaryPath.exists()
        or RestorePath.exists()
    ):
        raise ValueError(
            "compatibility promotion temporary path already exists; "
            "promotion refused"
        )
    ReplacedReference = False
    try:
        ReferenceWriter(StagingPath, CandidateReference)
        StagedReference = ReferenceReader(StagingPath)
        if (
            ReadNested(
                StagedReference,
                "FirstValidBaselines",
                CompatibilityCaseName,
            )
            != Entry
        ):
            raise ValueError(
                "staged compatibility first-valid baseline did not "
                "round-trip"
            )
        if sha256(PathValue.read_bytes()).hexdigest() != (
            ExpectedOriginalSha256
        ):
            raise ValueError(
                "baseline changed during compatibility promotion; overwrite "
                "refused"
            )
        StagingPath.replace(PathValue)
        ReplacedReference = True
        PublishedReference = ReferenceReader(PathValue)
        if (
            ReadNested(
                PublishedReference,
                "FirstValidBaselines",
                CompatibilityCaseName,
            )
            != Entry
        ):
            raise ValueError(
                "published compatibility first-valid baseline did not "
                "round-trip"
            )
        return PublishedReference
    except Exception:
        if ReplacedReference:
            try:
                RestorePath.write_bytes(OriginalBytes)
                RestorePath.replace(PathValue)
            finally:
                RestorePath.unlink(missing_ok=True)
        raise
    finally:
        StagingPath.unlink(missing_ok=True)
        WriterTemporaryPath.unlink(missing_ok=True)


def CompareCompatibility(
    Baseline: dict[str, object],
    Candidate: dict[str, object],
) -> dict[str, object]:
    """Compare only fields required for a meaningful speed/footprint gate."""
    Reference = Baseline.get("Compatibility")
    if not isinstance(Reference, dict):
        return {
            "Compatible": False,
            "MismatchFields": ["Compatibility"],
            "Reference": Reference,
            "Candidate": Candidate,
        }
    SourceProvenance = Baseline.get("SourceProvenance")
    LegacyV15Reference = bool(
        isinstance(SourceProvenance, dict)
        and SourceProvenance.get("ExpectedPolicyVersion")
        == BaselinePolicyVersion
    )
    def MatchesRequiredField(Name: str) -> bool:
        """Compare core provenance, tolerating only legacy generic CPU IDs."""
        if Name not in Reference or Name not in Candidate:
            return False
        if Reference[Name] == Candidate[Name]:
            return True
        if Name != "CpuProfile" or not LegacyV15Reference:
            return False
        ReferenceCpu = Reference[Name]
        CandidateCpu = Candidate[Name]
        if not isinstance(ReferenceCpu, dict) or not isinstance(
            CandidateCpu,
            dict,
        ):
            return False
        GenericModel = ReferenceCpu.get("Architecture")
        return (
            ReferenceCpu.get("Model") == GenericModel
            and set(ReferenceCpu) == {
                "Architecture",
                "LogicalCpuCount",
                "Model",
            }
            and CandidateCpu.get("Architecture")
            == ReferenceCpu.get("Architecture")
            and CandidateCpu.get("LogicalCpuCount")
            == ReferenceCpu.get("LogicalCpuCount")
            and bool(CandidateCpu.get("Model"))
            and bool(CandidateCpu.get("CpuFamily"))
        )

    MismatchFields = [
        Name
        for Name in RequiredCompatibilityFields
        if not MatchesRequiredField(Name)
    ]
    IgnoredLegacyAdditiveFields: list[str] = []
    for Name in LegacyAdditiveCompatibilityFields:
        if Name in Reference:
            if Name not in Candidate or Reference[Name] != Candidate[Name]:
                MismatchFields.append(Name)
            continue
        if LegacyV15Reference:
            if Name in Candidate:
                IgnoredLegacyAdditiveFields.append(Name)
            continue
        MismatchFields.append(Name)
    return {
        "Compatible": not MismatchFields,
        "MismatchFields": MismatchFields,
        "LegacyV15AdditiveCompatibilityApplied": bool(
            IgnoredLegacyAdditiveFields
        ),
        "IgnoredLegacyAdditiveFields": (
            IgnoredLegacyAdditiveFields
        ),
        "Reference": Reference,
        "Candidate": Candidate,
    }


def AppendRunFailure(
    Run: dict[str, object],
    Failure: str,
) -> None:
    """Attach an aggregate regression failure to one completed run."""
    Evaluation = Run.get("Evaluation")
    if not isinstance(Evaluation, dict):
        return
    Failures = Evaluation.setdefault("Failures", [])
    if isinstance(Failures, list) and Failure not in Failures:
        Failures.append(Failure)
    Evaluation["Accepted"] = False
    Run["Accepted"] = False
    Run["Status"] = "FAILED"


def BuildDiagnosticMetricComparison(
    BaselineMetrics: object,
    CandidateMetrics: object,
    MetricNames: tuple[str, ...],
) -> dict[str, dict[str, object]]:
    """Present baseline/candidate diagnostics without turning them into gates."""
    BaselineValues = (
        BaselineMetrics if isinstance(BaselineMetrics, dict) else {}
    )
    CandidateValues = (
        CandidateMetrics if isinstance(CandidateMetrics, dict) else {}
    )
    Result: dict[str, dict[str, object]] = {}
    for Name in MetricNames:
        BaselineValue = BaselineValues.get(Name)
        CandidateValue = CandidateValues.get(Name)
        Numeric = (
            isinstance(BaselineValue, (int, float))
            and not isinstance(BaselineValue, bool)
            and isinstance(CandidateValue, (int, float))
            and not isinstance(CandidateValue, bool)
        )
        Result[Name] = {
            "Baseline": BaselineValue,
            "Candidate": CandidateValue,
            "Delta": (
                CandidateValue - BaselineValue
                if Numeric
                else None
            ),
        }
    return Result


def BuildBaselineComparison(
    *,
    Baseline: dict[str, object],
    CandidateRuns: list[dict[str, object]],
    Compatibility: dict[str, object],
    CandidateSourceProvenance: dict[str, object],
    CaseNames: frozenset[str] = RegressionCaseNames,
    RequireWarmup: bool = True,
) -> dict[str, object]:
    """Apply strict footprint and bounded speed gates to measured candidates."""
    CircuitComparisons: dict[str, object] = {}
    BaselineCases = Baseline.get("Cases", {})
    if not isinstance(BaselineCases, dict):
        BaselineCases = {}
    WarmupRuns = [
        Run
        for Run in CandidateRuns
        if (
            Run.get("Warmup") is True
            and Run.get("Circuit") in CaseNames
        )
    ]
    WarmupsPassed = (
        not RequireWarmup
        or (
            len(WarmupRuns) == 1
            and all(bool(Run.get("Accepted")) for Run in WarmupRuns)
        )
    )
    OverallPassed = (
        Compatibility.get("Compatible") is True
        and WarmupsPassed
    )
    for Case in AcceptanceCases:
        if Case.Name not in CaseNames:
            continue
        CaseRuns = [
            Run
            for Run in CandidateRuns
            if (
                Run.get("Circuit") == Case.Name
                and Run.get("MeasurementIncluded") is True
            )
        ]
        CandidateSummary = BuildCaseBaselineSummary(Case, CandidateRuns)
        ReferenceSummary = BaselineCases.get(Case.Name, {})
        if not isinstance(ReferenceSummary, dict):
            ReferenceSummary = {}
        ReferenceFootprint = ReferenceSummary.get("FootprintMetrics", {})
        if not isinstance(ReferenceFootprint, dict):
            ReferenceFootprint = {}
        PerRunFootprint: list[dict[str, object]] = []
        FootprintPassed = len(CaseRuns) == Case.RequiredRuns
        for Run in CaseRuns:
            CandidateFootprint = ReadNested(
                Run,
                "Determinism",
                "Evidence",
                "FootprintMetrics",
            )
            if not isinstance(CandidateFootprint, dict):
                CandidateFootprint = {}
            Metrics: dict[str, object] = {}
            RunPassed = True
            for Name in FootprintMetricFields:
                BaselineValue = ReferenceFootprint.get(Name)
                CandidateValue = CandidateFootprint.get(Name)
                Passed = (
                    isinstance(BaselineValue, int)
                    and not isinstance(BaselineValue, bool)
                    and isinstance(CandidateValue, int)
                    and not isinstance(CandidateValue, bool)
                    and CandidateValue <= BaselineValue
                )
                Metrics[Name] = {
                    "Baseline": BaselineValue,
                    "Candidate": CandidateValue,
                    "Delta": (
                        CandidateValue - BaselineValue
                        if isinstance(BaselineValue, int)
                        and isinstance(CandidateValue, int)
                        else None
                    ),
                    "Passed": Passed,
                }
                RunPassed = RunPassed and Passed
            if not RunPassed:
                AppendRunFailure(
                    Run,
                    "footprint regression against captured baseline",
                )
            FootprintPassed = FootprintPassed and RunPassed
            PerRunFootprint.append({
                "RunName": Run.get("RunName"),
                "Dimensions": {
                    Name: CandidateFootprint.get(Name)
                    for Name in DimensionMetricFields
                },
                "Metrics": Metrics,
                "Passed": RunPassed,
            })

        ReferenceRuntime = ReferenceSummary.get("Runtime", {})
        CandidateRuntime = CandidateSummary.get("Runtime", {})
        if not isinstance(ReferenceRuntime, dict):
            ReferenceRuntime = {}
        if not isinstance(CandidateRuntime, dict):
            CandidateRuntime = {}
        BaselineMedian = ReferenceRuntime.get(
            "MedianSecondsRaw",
            ReferenceRuntime.get("ReferenceMedianLowerBoundSeconds"),
        )
        CandidateMedian = CandidateRuntime.get("MedianSecondsRaw")
        SpeedDelta = (
            float(CandidateMedian) - float(BaselineMedian)
            if isinstance(BaselineMedian, (int, float))
            and isinstance(CandidateMedian, (int, float))
            else None
        )
        SpeedFraction = (
            SpeedDelta / float(BaselineMedian)
            if SpeedDelta is not None
            and isinstance(BaselineMedian, (int, float))
            and float(BaselineMedian) > 0.0
            else None
        )
        ReferenceStable = (
            ReferenceRuntime.get("Stable")
            if isinstance(
                ReferenceRuntime.get("SamplesSecondsRaw"),
                list,
            )
            else ReferenceRuntime.get("StableConservative")
        )
        MeasurementValid = (
            ReferenceStable is True
            and CandidateRuntime.get("Stable") is True
        )
        QuietRerunRequired = (
            ReferenceStable is False
            or CandidateRuntime.get("Stable") is False
        )
        SpeedPassed = (
            MeasurementValid
            and SpeedFraction is not None
            and isinstance(BaselineMedian, (int, float))
            and isinstance(CandidateMedian, (int, float))
            and float(CandidateMedian)
            <= float(BaselineMedian)
            * (1.0 + MaximumRuntimeRegressionFraction)
        )
        if not SpeedPassed:
            RuntimeFailure = (
                "runtime measurement invalid; quiet rerun required"
                if QuietRerunRequired
                else "runtime median regression against captured baseline"
            )
            for Run in CaseRuns:
                AppendRunFailure(
                    Run,
                    RuntimeFailure,
                )

        # BuildCaseBaselineSummary was computed before aggregate regression
        # failures were attached. Its promotability therefore captures the
        # authoritative accuracy/determinism gate.
        AccuracyPassed = bool(
            CandidateSummary.get("AccuracyAndDeterminismPassed")
        )
        ReferenceSimulationBackend = ReferenceSummary.get(
            "SimulationBackend"
        )
        CandidateSimulationBackend = CandidateSummary.get(
            "SimulationBackend"
        )
        SimulationBackendCompatible = (
            isinstance(ReferenceSimulationBackend, str)
            and isinstance(CandidateSimulationBackend, str)
            and CandidateSimulationBackend == ReferenceSimulationBackend
        )
        if not SimulationBackendCompatible:
            for Run in CaseRuns:
                AppendRunFailure(
                    Run,
                    "simulation backend differs from captured baseline",
                )
        CircuitPassed = (
            AccuracyPassed
            and FootprintPassed
            and SpeedPassed
            and SimulationBackendCompatible
        )
        OverallPassed = OverallPassed and CircuitPassed
        CircuitComparisons[Case.Name] = {
            "Passed": CircuitPassed,
            "AccuracyAndDeterminismPassed": AccuracyPassed,
            "FootprintPassed": FootprintPassed,
            "SpeedPassed": SpeedPassed,
            "SimulationBackendCompatible": (
                SimulationBackendCompatible
            ),
            "SimulationBackend": {
                "Baseline": ReferenceSimulationBackend,
                "Candidate": CandidateSimulationBackend,
            },
            "MeasurementValid": MeasurementValid,
            "QuietRerunRequired": QuietRerunRequired,
            "Footprint": PerRunFootprint,
            "Dimensions": BuildDiagnosticMetricComparison(
                ReferenceSummary.get("Dimensions"),
                CandidateSummary.get("Dimensions"),
                DimensionMetricFields,
            ),
            "RouteMetrics": BuildDiagnosticMetricComparison(
                ReferenceSummary.get("RouteMetrics"),
                CandidateSummary.get("RouteMetrics"),
                RouteMetricFields,
            ),
            "Runtime": {
                "BaselineMedianSecondsRaw": BaselineMedian,
                "CandidateMedianSecondsRaw": CandidateMedian,
                "BaselineMedianSeconds": (
                    round(float(BaselineMedian), 6)
                    if isinstance(BaselineMedian, (int, float))
                    else None
                ),
                "CandidateMedianSeconds": (
                    round(float(CandidateMedian), 6)
                    if isinstance(CandidateMedian, (int, float))
                    else None
                ),
                "DeltaSecondsRaw": SpeedDelta,
                "DeltaSeconds": (
                    round(SpeedDelta, 6)
                    if SpeedDelta is not None
                    else None
                ),
                "DeltaFractionRaw": SpeedFraction,
                "DeltaFraction": (
                    round(SpeedFraction, 9)
                    if SpeedFraction is not None
                    else None
                ),
                "MaximumAllowedRegressionFraction": (
                    MaximumRuntimeRegressionFraction
                ),
                "BaselineSpreadFraction": ReferenceRuntime.get(
                    "MaximumSpreadFraction"
                ),
                "CandidateSpreadFraction": CandidateRuntime.get(
                    "MaximumSpreadFraction"
                ),
                "BaselineSpreadFractionRaw": ReferenceRuntime.get(
                    "MaximumSpreadFractionRaw",
                    ReferenceRuntime.get(
                        "MaximumSpreadFractionConservative"
                    ),
                ),
                "CandidateSpreadFractionRaw": CandidateRuntime.get(
                    "MaximumSpreadFractionRaw",
                    CandidateRuntime.get("MaximumSpreadFraction"),
                ),
                "MaximumAllowedSpreadFraction": (
                    MaximumRuntimeSpreadFraction
                ),
                "MeasurementValid": MeasurementValid,
                "QuietRerunRequired": QuietRerunRequired,
            },
        }

    BaselineProvenance = Baseline.get("SourceProvenance", {})
    if not isinstance(BaselineProvenance, dict):
        BaselineProvenance = {}
    MeasurementsValid = all(
        bool(Comparison.get("MeasurementValid"))
        for Comparison in CircuitComparisons.values()
        if isinstance(Comparison, dict)
    )
    QuietRerunRequired = any(
        bool(Comparison.get("QuietRerunRequired"))
        for Comparison in CircuitComparisons.values()
        if isinstance(Comparison, dict)
    )
    return {
        "SchemaVersion": "router-baseline-comparison-v1",
        "Mode": "compare",
        "Passed": OverallPassed,
        "WarmupsPassed": WarmupsPassed,
        "MeasurementValid": MeasurementsValid,
        "QuietRerunRequired": QuietRerunRequired,
        "Compatibility": Compatibility,
        "SourceDifferencesAllowed": {
            "GitRevisionChanged": ReadNested(
                BaselineProvenance,
                "Git",
                "Revision",
            )
            != ReadNested(
                CandidateSourceProvenance,
                "Git",
                "Revision",
            ),
            "SourceContentChanged": ReadNested(
                BaselineProvenance,
                "SourceContent",
                "AggregateSha256",
            )
            != ReadNested(
                CandidateSourceProvenance,
                "SourceContent",
                "AggregateSha256",
            ),
            "NativeExtensionChanged": ReadNested(
                BaselineProvenance,
                "NativeExtension",
                "Sha256",
            )
            != ReadNested(
                CandidateSourceProvenance,
                "NativeExtension",
                "Sha256",
            ),
            "PolicyFingerprintChanged": ReadNested(
                BaselineProvenance,
                "Policy",
                "Sha256",
            )
            != ReadNested(
                CandidateSourceProvenance,
                "Policy",
                "Sha256",
            ),
            "PolicyVersionChanged": BaselineProvenance.get(
                "ExpectedPolicyVersion"
            )
            != CandidateSourceProvenance.get("ExpectedPolicyVersion"),
        },
        "Circuits": CircuitComparisons,
        "UnbaselinedCircuits": (
            sorted(ExtendedCaseNames.difference(CaseNames))
            if ExtendedCaseNames
            else []
        ),
    }


def RunAcceptance(
    Configuration: AcceptanceConfiguration,
    *,
    CommandRunner: Callable[..., AcceptanceCommandResult] = RunCompilerCommand,
    SourceStateProvider: Callable[[Path], dict[str, object]] = ReadSourceState,
    SourceProvenanceProvider: Callable[
        [AcceptanceConfiguration, dict[str, object]],
        dict[str, object],
    ] = BuildSourceProvenance,
    DesignDigestBuilder: Callable[[Path], str] = BuildEmittedDesignDigest,
    LitematicCompositionEvidenceBuilder: Callable[
        [Path], dict[str, int]
    ] = BuildLitematicCompositionEvidence,
    TruthTableEvidenceBuilder: Callable[
        [Path], dict[str, object]
    ] = BuildTruthTableSemanticEvidence,
    UtcNowProvider: Callable[[], str] = UtcNow,
    BaselineReferenceReader: Callable[
        [Path], dict[str, object]
    ] = ReadBaselineReference,
    BaselineReferenceWriter: Callable[
        [Path, dict[str, object]], None
    ] = WriteManifest,
) -> dict[str, object]:
    """Execute the configured matrix serially and persist complete evidence."""
    PlannedRuns = BuildPlannedRuns(Configuration)
    PlannedCases = _BuildPlannedCases(Configuration)
    SourceState = SourceStateProvider(Configuration.RepositoryRoot)
    Environment = BuildEnvironmentRecord(Configuration)
    SourceProvenance = SourceProvenanceProvider(
        Configuration,
        SourceState,
    )
    ComparisonCompatibility = BuildComparisonCompatibility(
        Environment=Environment,
        SourceProvenance=SourceProvenance,
    )
    RequestedExtendedCaseNames = frozenset(
        Case.Name
        for Case in PlannedCases
        if Case.Name in ExtendedCaseNames
    )
    def BuildRequestedCases() -> list[dict[str, object]]:
        return [
            Case.ToDictionary()
            for Case in PlannedCases
        ]
    Manifest: dict[str, object] = {
        "SchemaVersion": AcceptanceManifestSchemaVersion,
        "Status": "DRY_RUN" if Configuration.DryRun else "RUNNING",
        "Accepted": False,
        "ExecutionMode": "sequential",
        "BaselineMode": Configuration.BaselineMode,
        "BaselinePath": (
            str(Configuration.BaselinePath.resolve(strict=False))
            if Configuration.BaselinePath is not None
            else None
        ),
        "RoutingDeadlinePolicy": {
            "Mode": "wall-ceiling-minus-publication-reserve",
            "DefaultPublicationReserveSeconds": (
                DefaultRoutingPublicationReserveSeconds
            ),
            "WallRuntimeCeilingsUnchanged": True,
            "SubprocessFinalizationGraceSeconds": (
                SubprocessFinalizationGraceSeconds
            ),
            "CaptureTimeoutGraceSeconds": (
                Configuration.CaptureTimeoutGraceSeconds
            ),
        },
        "SubprocessDeadlineGraceSeconds": (
            Configuration.CaptureTimeoutGraceSeconds
            if Configuration.BaselineMode == "capture"
            else 0.0
        ),
        "MaximumDeadlineOverrunSeconds": MaximumDeadlineOverrunSeconds,
        "StartedAtUtc": UtcNowProvider(),
        "CompletedAtUtc": None,
        "RecoveryRoot": str(Configuration.RecoveryRoot),
        "ManifestPath": str(Configuration.ManifestPath),
        "SourceState": SourceState,
        "SourceProvenance": SourceProvenance,
        "SourceProvenanceStable": None,
        "ProvenanceChecks": [],
        "Environment": Environment,
        "ComparisonCompatibility": ComparisonCompatibility,
        "Cases": BuildRequestedCases(),
        "BaselineComparison": {
            "Mode": Configuration.BaselineMode or "disabled",
            "Passed": None,
            "MeasurementValid": None,
            "QuietRerunRequired": False,
            "Circuits": {},
            "UnbaselinedCircuits": (
                sorted(RequestedExtendedCaseNames)
                if Configuration.BaselineMode == "compare"
                else []
            ),
        },
        "Runs": PlannedRuns,
    }
    ReferenceAlreadyExists = bool(
        Configuration.BaselineMode == "capture"
        and Configuration.BaselinePath is not None
        and Configuration.BaselinePath.exists()
    )
    RawCaptureEvidenceAlreadyExists = bool(
        Configuration.BaselineMode == "capture"
        and Configuration.RecoveryRoot.is_dir()
        and any(Configuration.RecoveryRoot.iterdir())
    )
    if ReferenceAlreadyExists or RawCaptureEvidenceAlreadyExists:
        # A promoted baseline is immutable evidence. Refuse before writing the
        # session manifest or clearing same-named run directories so an
        # accidental recapture under a distinct reference filename cannot
        # destroy either the compact reference or its full raw artifacts.
        FailureReasons = []
        if ReferenceAlreadyExists:
            FailureReasons.append("baseline reference already exists")
        if RawCaptureEvidenceAlreadyExists:
            FailureReasons.append("raw baseline capture evidence already exists")
        Manifest["BaselineComparison"] = {
            "SchemaVersion": "router-baseline-comparison-v1",
            "Mode": "capture",
            "Passed": False,
            "Promotable": False,
            "MeasurementValid": False,
            "QuietRerunRequired": False,
            "ReferenceWritten": False,
            "ReferenceAlreadyExists": ReferenceAlreadyExists,
            "RawEvidenceAlreadyExists": (
                RawCaptureEvidenceAlreadyExists
            ),
            "OverwriteBlocked": True,
            "ReferencePath": str(
                Configuration.BaselinePath.resolve(strict=False)
            ),
            "Failure": "; ".join(FailureReasons) + "; capture refused",
            "Circuits": {},
            "UnbaselinedCircuits": sorted(ExtendedCaseNames),
        }
        Manifest["Runs"] = [
            {
                **Run,
                "Status": "SKIPPED",
                "Accepted": False,
                "SkipReason": "; ".join(FailureReasons),
            }
            for Run in PlannedRuns
        ]
        Manifest["Status"] = "FAILED"
        Manifest["CompletedAtUtc"] = UtcNowProvider()
        return Manifest
    RawComparisonEvidenceAlreadyExists = bool(
        Configuration.BaselineMode == "compare"
        and Configuration.RecoveryRoot.is_dir()
        and any(Configuration.RecoveryRoot.iterdir())
    )
    if RawComparisonEvidenceAlreadyExists:
        # Candidate evidence is also immutable. A quiet rerun or retry must
        # choose a fresh output root/date rather than erasing the observation
        # that caused the rerun.
        Failure = "raw candidate comparison evidence already exists"
        Manifest["BaselineComparison"] = {
            "SchemaVersion": "router-baseline-comparison-v1",
            "Mode": "compare",
            "Passed": False,
            "MeasurementValid": False,
            "QuietRerunRequired": False,
            "RawEvidenceAlreadyExists": True,
            "OverwriteBlocked": True,
            "ReferencePath": str(
                Configuration.BaselinePath.resolve(strict=False)
            ),
            "Failure": Failure + "; comparison refused",
            "Circuits": {},
            "UnbaselinedCircuits": sorted(ExtendedCaseNames),
        }
        Manifest["Runs"] = [
            {
                **Run,
                "Status": "SKIPPED",
                "Accepted": False,
                "SkipReason": Failure,
            }
            for Run in PlannedRuns
        ]
        Manifest["Status"] = "FAILED"
        Manifest["CompletedAtUtc"] = UtcNowProvider()
        return Manifest
    WriteManifest(Configuration.ManifestPath, Manifest)
    if Configuration.DryRun:
        Manifest["CompletedAtUtc"] = UtcNowProvider()
        WriteManifest(Configuration.ManifestPath, Manifest)
        return Manifest

    BaselineReference: dict[str, object] | None = None
    BaselineCompatibility: dict[str, object] | None = None
    BaselineReferenceSha256: str | None = None
    BaselineAvailableBeforeRun = False
    if Configuration.BaselineMode == "compare":
        assert Configuration.BaselinePath is not None
        try:
            BaselineReferenceBytes = Configuration.BaselinePath.read_bytes()
            BaselineReferenceSha256 = sha256(
                BaselineReferenceBytes
            ).hexdigest()
            BaselineReference = BaselineReferenceReader(
                Configuration.BaselinePath
            )
            FirstValidBaselines = BaselineReference.get(
                "FirstValidBaselines"
            )
            BaselineAvailableBeforeRun = bool(
                isinstance(FirstValidBaselines, dict)
                and any(
                    Name in FirstValidBaselines
                    for Name in ExtendedCaseNames
                )
            )
            BaselineCompatibility = CompareCompatibility(
                BaselineReference,
                ComparisonCompatibility,
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
        ) as Error:
            Manifest["BaselineComparison"] = {
                "SchemaVersion": "router-baseline-comparison-v1",
                "Mode": "compare",
                "Passed": False,
                "MeasurementValid": False,
                "QuietRerunRequired": False,
                "Failure": f"could not load baseline: {Error}",
                "Circuits": {},
                "UnbaselinedCircuits": sorted(ExtendedCaseNames),
            }
            Manifest["Runs"] = [
                {
                    **Run,
                    "Status": "SKIPPED",
                    "Accepted": False,
                    "SkipReason": "baseline reference could not be loaded",
                }
                for Run in PlannedRuns
            ]
            Manifest["Status"] = "FAILED"
            Manifest["CompletedAtUtc"] = UtcNowProvider()
            WriteManifest(Configuration.ManifestPath, Manifest)
            return Manifest
        if BaselineCompatibility.get("Compatible") is not True:
            Manifest["BaselineComparison"] = {
                "SchemaVersion": "router-baseline-comparison-v1",
                "Mode": "compare",
                "Passed": False,
                "MeasurementValid": False,
                "QuietRerunRequired": False,
                "Failure": "baseline environment is incompatible",
                "Compatibility": BaselineCompatibility,
                "Circuits": {},
                "UnbaselinedCircuits": sorted(ExtendedCaseNames),
            }
            Manifest["Runs"] = [
                {
                    **Run,
                    "Status": "SKIPPED",
                    "Accepted": False,
                    "SkipReason": "baseline environment is incompatible",
                }
                for Run in PlannedRuns
            ]
            Manifest["Status"] = "FAILED"
            Manifest["CompletedAtUtc"] = UtcNowProvider()
            WriteManifest(Configuration.ManifestPath, Manifest)
            return Manifest

    Baselines: dict[str, tuple[str, dict[str, object]]] = {}
    ChildEnvironment = BuildChildEnvironment(Configuration)
    CompletedRuns: list[dict[str, object]] = []

    def CheckSourceProvenance(Phase: str) -> bool:
        """Re-snapshot mutable implementation inputs around long matrices."""
        EndSourceState = SourceStateProvider(Configuration.RepositoryRoot)
        EndSourceProvenance = SourceProvenanceProvider(
            Configuration,
            EndSourceState,
        )
        Stable = (
            EndSourceState == SourceState
            and EndSourceProvenance == SourceProvenance
        )
        Checks = Manifest.setdefault("ProvenanceChecks", [])
        if isinstance(Checks, list):
            Checks.append({
                "Phase": Phase,
                "Stable": Stable,
                "SourceState": EndSourceState,
                "SourceProvenance": EndSourceProvenance,
            })
        Manifest["SourceProvenanceStable"] = (
            Stable
            if Manifest.get("SourceProvenanceStable") is None
            else bool(Manifest["SourceProvenanceStable"]) and Stable
        )
        return Stable

    def ExecuteOne(
        Planned: dict[str, object],
    ) -> dict[str, object]:
        Case = next(
            Value for Value in AcceptanceCases
            if Value.Name == Planned["Circuit"]
        )
        RunName = str(Planned["RunName"])
        RunDirectory = Configuration.RecoveryRoot / RunName
        Artifacts = BuildRunArtifacts(RunDirectory, RunName)
        RunDirectory.mkdir(parents=True, exist_ok=True)
        ClearPriorRunArtifacts(Artifacts)
        Command = list(Planned["Command"])
        StartedAtUtc = UtcNowProvider()
        try:
            Process = CommandRunner(
                Command=Command,
                WorkingDirectory=Configuration.RepositoryRoot,
                Environment=ChildEnvironment,
                TimeoutSeconds=BuildSubprocessTimeoutSeconds(
                    Case,
                    Configuration,
                ),
            )
        except Exception as Error:
            Process = AcceptanceCommandResult(
                ReturnCode=127,
                Stdout="",
                Stderr=f"acceptance command runner failed: {Error}\n",
                RuntimeSeconds=0.0,
            )
        Artifacts["Stdout"].write_text(Process.Stdout, encoding="utf-8")
        Artifacts["Stderr"].write_text(Process.Stderr, encoding="utf-8")
        Evaluation, Evidence = EvaluateRun(
            Case=Case,
            Process=Process,
            Artifacts=Artifacts,
            ExpectedSeed=Configuration.ExpectedSeed,
            ExpectedPolicyVersion=Configuration.ExpectedPolicyVersion,
            DesignDigestBuilder=DesignDigestBuilder,
            LitematicCompositionEvidenceBuilder=(
                LitematicCompositionEvidenceBuilder
            ),
            TruthTableEvidenceBuilder=TruthTableEvidenceBuilder,
        )
        ExactInterfaceProofCheckpoint = None
        if (
            Configuration.IncludeCla4
            and Case.Name in ExtendedCaseNames
        ):
            ExactInterfaceProofCheckpoint = (
                EvaluateExactInterfaceProofCheckpoint(Artifacts)
            )
            Evaluation[ExactInterfaceProofCheckpointField] = (
                ExactInterfaceProofCheckpoint
            )
            if not ExactInterfaceProofCheckpoint["Accepted"]:
                Evaluation["Failures"].extend(
                    ExactInterfaceProofCheckpoint.get("Failures", [])
                )
                Evaluation["Accepted"] = False

        Determinism = {
            "BaselineRun": None,
            "MatchesBaseline": None,
            "MismatchFields": [],
            "Evidence": Evidence,
            "ExcludedAsWarmup": Planned.get("Warmup") is True,
        }
        if Evidence is not None and Planned.get("MeasurementIncluded") is True:
            Baseline = Baselines.get(Case.Name)
            if Baseline is None:
                Baselines[Case.Name] = (RunName, Evidence)
                Determinism.update({
                    "BaselineRun": RunName,
                    "MatchesBaseline": True,
                })
            else:
                BaselineName, BaselineEvidence = Baseline
                MismatchFields = [
                    Name
                    for Name in DeterministicEvidenceFields
                    if Evidence.get(Name) != BaselineEvidence.get(Name)
                ]
                Determinism.update({
                    "BaselineRun": BaselineName,
                    "MatchesBaseline": not MismatchFields,
                    "MismatchFields": MismatchFields,
                })
                for Name in MismatchFields:
                    Evaluation["Failures"].append(
                        f"determinism mismatch: {Name}"
                    )
                Evaluation["Accepted"] = not Evaluation["Failures"]

        Completed = {
            **Planned,
            "Status": "PASSED" if Evaluation["Accepted"] else "FAILED",
            "Accepted": Evaluation["Accepted"],
            "StartedAtUtc": StartedAtUtc,
            "CompletedAtUtc": UtcNowProvider(),
            "EnvironmentOverrides": {
                "PYTHONHASHSEED": ChildEnvironment["PYTHONHASHSEED"],
                **(
                    {
                        "RC_ROUTING_THREADS": ChildEnvironment[
                            "RC_ROUTING_THREADS"
                        ]
                    }
                    if "RC_ROUTING_THREADS" in ChildEnvironment
                    else {}
                ),
            },
            "Evaluation": Evaluation,
            ExactInterfaceProofCheckpointField: (
                ExactInterfaceProofCheckpoint
            ),
            "Determinism": Determinism,
        }
        return Completed

    RegressionPlanned = [
        Run
        for Run in PlannedRuns
        if Run.get("Circuit") in RegressionCaseNames
    ]
    ExtendedPlanned = [
        Run
        for Run in PlannedRuns
        if Run.get("Circuit") in ExtendedCaseNames
    ]
    if Configuration.BaselineMode in {"capture", "compare"}:
        RunsToExecuteFirst = RegressionPlanned
    else:
        RunsToExecuteFirst = PlannedRuns

    for Planned in RunsToExecuteFirst:
        Completed = ExecuteOne(Planned)
        CompletedRuns.append(Completed)
        Manifest["Runs"] = [
            *CompletedRuns,
            *PlannedRuns[len(CompletedRuns):],
        ]
        WriteManifest(Configuration.ManifestPath, Manifest)
        if (
            Configuration.BaselineMode is None
            and not Completed["Accepted"]
        ):
            break

    if Configuration.BaselineMode == "capture":
        CheckSourceProvenance("after-regression-capture")
        Manifest["CompletedAtUtc"] = UtcNowProvider()
        Baseline = BuildBaselineReference({
            **Manifest,
            "Runs": CompletedRuns,
        })
        BaselineWritten = False
        assert Configuration.BaselinePath is not None
        ReferenceAlreadyExists = Configuration.BaselinePath.exists()
        OverwriteBlocked = bool(
            Baseline["Promotable"] and ReferenceAlreadyExists
        )
        if Baseline["Promotable"] and not ReferenceAlreadyExists:
            BaselineReferenceWriter(Configuration.BaselinePath, Baseline)
            BaselineWritten = True
        CapturePassed = bool(Baseline["Promotable"] and BaselineWritten)
        CaptureMeasurementValid = all(
            isinstance(CaseSummary, dict)
            and ReadNested(CaseSummary, "Runtime", "Stable") is True
            for CaseSummary in Baseline["Cases"].values()
        )
        Manifest["BaselineComparison"] = {
            "SchemaVersion": "router-baseline-comparison-v1",
            "Mode": "capture",
            "Passed": CapturePassed,
            "Promotable": Baseline["Promotable"],
            "MeasurementValid": CaptureMeasurementValid,
            "QuietRerunRequired": not CaptureMeasurementValid,
            "ReferenceWritten": BaselineWritten,
            "ReferenceAlreadyExists": ReferenceAlreadyExists,
            "RawEvidenceAlreadyExists": False,
            "OverwriteBlocked": OverwriteBlocked,
            "ReferencePath": str(
                Configuration.BaselinePath.resolve(strict=False)
            ),
            "Circuits": Baseline["Cases"],
            "UnbaselinedCircuits": sorted(ExtendedCaseNames),
        }
        Manifest["Runs"] = CompletedRuns
        Manifest["Accepted"] = CapturePassed
        Manifest["Status"] = (
            "PASSED" if Manifest["Accepted"] else "FAILED"
        )
        WriteManifest(Configuration.ManifestPath, Manifest)
        return Manifest

    if Configuration.BaselineMode == "compare":
        assert BaselineReference is not None
        assert BaselineCompatibility is not None
        RegressionProvenanceStable = CheckSourceProvenance(
            "after-regression-comparison"
        )
        Comparison = BuildBaselineComparison(
            Baseline=BaselineReference,
            CandidateRuns=CompletedRuns,
            Compatibility=BaselineCompatibility,
            CandidateSourceProvenance=SourceProvenance,
        )
        Comparison["BaselineAvailableBeforeRun"] = (
            BaselineAvailableBeforeRun
        )
        Comparison["ReferenceSha256BeforeRun"] = (
            BaselineReferenceSha256
        )
        ExistingFirstValidBaselines = BaselineReference.get(
            "FirstValidBaselines"
        )
        ExistingCompatibilityBaselines = (
            ExistingFirstValidBaselines
            if isinstance(ExistingFirstValidBaselines, dict)
            else {}
        )
        ExtendedCases = [
            Case
            for Case in AcceptanceCases
            if Case.Name in RequestedExtendedCaseNames
        ]
        if len(ExtendedCases) != len(RequestedExtendedCaseNames):
            raise ValueError(
                "unsupported acceptance matrix: missing "
                "requested extended case"
            )
        BaselineAvailableBeforeRun = bool(
            ExistingCompatibilityBaselines
            and any(
                Name in ExistingCompatibilityBaselines
                for Name in RequestedExtendedCaseNames
            )
        )
        Comparison["FirstValidBaselines"] = {
            Name: deepcopy(
                ExistingCompatibilityBaselines[Name]
            )
            for Name in RequestedExtendedCaseNames
            if Name in ExistingCompatibilityBaselines
            and isinstance(
                ExistingCompatibilityBaselines[Name],
                dict,
            )
        }
        UnbaselinedCompatibility = list(
            RequestedExtendedCaseNames.difference(
                Comparison["FirstValidBaselines"]
            )
        )
        Comparison["UnbaselinedCircuits"] = (
            UnbaselinedCompatibility
        )
        Comparison["CompatibilityPromotionPassed"] = False
        Comparison["CompatibilityBaselineComparison"] = None
        Comparison["CompatibilityCandidateBaseline"] = None
        Comparison["ProvenanceStable"] = RegressionProvenanceStable
        if not RegressionProvenanceStable:
            Comparison["Passed"] = False
            Comparison["Failure"] = (
                "source/native/policy provenance changed during regression"
            )
        Comparison["ProtectedRegressionPassed"] = bool(
            Comparison.get("Passed")
        )
        Manifest["BaselineComparison"] = Comparison
        if Comparison["Passed"]:
            for Planned in ExtendedPlanned:
                Completed = ExecuteOne(Planned)
                CompletedRuns.append(Completed)
                Manifest["Runs"] = [
                    *CompletedRuns,
                    *PlannedRuns[len(CompletedRuns):],
                ]
                WriteManifest(Configuration.ManifestPath, Manifest)
            FinalProvenanceStable = CheckSourceProvenance(
                "after-extended-comparison"
            )
            Comparison["ProvenanceStable"] = (
                bool(Comparison.get("ProvenanceStable"))
                and FinalProvenanceStable
            )
            if not FinalProvenanceStable:
                Comparison["Passed"] = False
                Comparison["Failure"] = (
                    "source/native/policy provenance changed during "
                    "extended comparison"
                )
            for CompatibilityCase in ExtendedCases:
                CompatibilityCaseName = CompatibilityCase.Name
                ExistingCompatibilityEntry = (
                    ExistingCompatibilityBaselines.get(
                        CompatibilityCaseName
                    )
                    if isinstance(
                        ExistingCompatibilityBaselines,
                        dict,
                    )
                    else None
                )
                CompatibilityBaseline = BuildCaseBaselineSummary(
                    CompatibilityCase,
                    CompletedRuns,
                )
                CompatibilityCandidateBaseline = {
                    **CompatibilityBaseline,
                    "CapturedAtUtc": UtcNowProvider(),
                    "SourceProvenance": SourceProvenance,
                }
                Comparison["CompatibilityCandidateBaseline"] = (
                    CompatibilityCandidateBaseline
                )
                CompatibilityMeasurementValid = (
                    ReadNested(
                        CompatibilityBaseline,
                        "Runtime",
                        "Stable",
                    )
                    is True
                )
                Comparison["MeasurementValid"] = (
                    bool(Comparison.get("MeasurementValid"))
                    and CompatibilityMeasurementValid
                )
                Comparison["QuietRerunRequired"] = (
                    bool(Comparison.get("QuietRerunRequired"))
                    or not CompatibilityMeasurementValid
                )
                CompatibilityEvidencePromotable = bool(
                    CompatibilityBaseline.get("Promotable")
                )
                if (
                    not CompatibilityEvidencePromotable
                    and not isinstance(
                        ExistingCompatibilityEntry,
                        dict,
                    )
                    and Comparison.get("Failure") is None
                ):
                    Comparison["Passed"] = False
                    Comparison["Failure"] = (
                        f"{CompatibilityCaseName} repeated physical baseline "
                        "is not promotable"
                    )
                    Comparison["FirstValidBaselinePromotion"] = {
                        "Attempted": False,
                        "Written": False,
                        "ReferenceAlreadyAvailable": False,
                        "OverwriteBlocked": False,
                        "BaselineAvailableBeforeRun": False,
                    }
                elif isinstance(ExistingCompatibilityEntry, dict):
                    StoredCompatibility = CompareCompatibility(
                        {
                            "Compatibility": ExistingCompatibilityEntry[
                                "Compatibility"
                            ]
                        },
                        ComparisonCompatibility,
                    )
                    CompatibilityCircuitComparison = BuildBaselineComparison(
                        Baseline={
                            "Cases": {
                                CompatibilityCase.Name: (
                                    ExistingCompatibilityEntry[
                                        "CaseSummary"
                                    ]
                                )
                            },
                            "SourceProvenance": (
                                ExistingCompatibilityEntry[
                                    "SourceProvenance"
                                ]
                            ),
                        },
                        CandidateRuns=CompletedRuns,
                        Compatibility=StoredCompatibility,
                        CandidateSourceProvenance=SourceProvenance,
                        CaseNames=frozenset({CompatibilityCaseName}),
                        RequireWarmup=False,
                    )
                    CompatibilityCircuitComparison[
                        "BaselineAvailableBeforeRun"
                    ] = True
                    CompatibilityCircuitComparison[
                        "ReferencePromotedAtUtc"
                    ] = ExistingCompatibilityEntry["PromotedAtUtc"]
                    CompatibilityCircuitComparison["ProvenanceStable"] = (
                        bool(Comparison.get("ProvenanceStable"))
                    )
                    CompatibilityComparisonPassed = bool(
                        CompatibilityCircuitComparison.get("Passed")
                        and Comparison.get("ProvenanceStable")
                    )
                    CompatibilityCircuitComparison["Passed"] = (
                        CompatibilityComparisonPassed
                    )
                    Comparison["CompatibilityBaselineComparison"] = (
                        CompatibilityCircuitComparison
                    )
                    CompatibilityCaseComparison = ReadNested(
                        CompatibilityCircuitComparison,
                        "Circuits",
                        CompatibilityCase.Name,
                    )
                    if isinstance(
                        CompatibilityCaseComparison,
                        dict,
                    ):
                        Comparison["Circuits"][
                            CompatibilityCase.Name
                        ] = CompatibilityCaseComparison
                    Comparison["MeasurementValid"] = (
                        bool(Comparison.get("MeasurementValid"))
                        and bool(
                            CompatibilityCircuitComparison.get(
                                "MeasurementValid"
                            )
                        )
                    )
                    Comparison["QuietRerunRequired"] = (
                        bool(Comparison.get("QuietRerunRequired"))
                        or bool(
                            CompatibilityCircuitComparison.get(
                                "QuietRerunRequired"
                            )
                        )
                    )
                    Comparison["Passed"] = bool(
                        Comparison.get("Passed")
                        and CompatibilityComparisonPassed
                    )
                    Comparison["FirstValidBaselinePromotion"] = {
                        "Attempted": False,
                        "Written": False,
                        "ReferenceAlreadyAvailable": True,
                        "OverwriteBlocked": True,
                        "BaselineAvailableBeforeRun": True,
                    }
                    if (
                        not CompatibilityComparisonPassed
                        and Comparison.get("Failure") is None
                    ):
                        Comparison["Failure"] = (
                            f"{CompatibilityCaseName} regressed against its "
                            "first-valid baseline"
                        )
                elif (
                    Comparison.get("Passed")
                    and CompatibilityEvidencePromotable
                    and Comparison.get("ProvenanceStable")
                ):
                    assert Configuration.BaselinePath is not None
                    assert BaselineReferenceSha256 is not None
                    PromotedAtUtc = UtcNowProvider()
                    Entry = BuildFirstValidBaselineEntry(
                        Case=CompatibilityCase,
                        CaseSummary=CompatibilityBaseline,
                        SourceProvenance=SourceProvenance,
                        Environment=Environment,
                        Compatibility=ComparisonCompatibility,
                        ProvenanceChecks=(
                            Manifest["ProvenanceChecks"]
                            if isinstance(
                                Manifest.get("ProvenanceChecks"),
                                list,
                            )
                            else []
                        ),
                        PromotedAtUtc=PromotedAtUtc,
                        OriginalReferenceSha256=BaselineReferenceSha256,
                        OriginalReferenceContentSha256=(
                            sha256(EncodeManifest(BaselineReference)).hexdigest()
                        ),
                        OriginalFirstValidBaselinesFieldPresent=(
                            "FirstValidBaselines" in BaselineReference
                        ),
                    )
                    PromotionFailure: str | None = None
                    try:
                        PublishedReference = PromoteFirstValidBaseline(
                            PathValue=Configuration.BaselinePath,
                            Baseline=BaselineReference,
                            Entry=Entry,
                            ExpectedOriginalSha256=BaselineReferenceSha256,
                            ReferenceReader=BaselineReferenceReader,
                            ReferenceWriter=BaselineReferenceWriter,
                            CompatibilityCaseName=CompatibilityCaseName,
                        )
                        PublishedEntry = ReadNested(
                            PublishedReference,
                            "FirstValidBaselines",
                            CompatibilityCase.Name,
                        )
                        if PublishedEntry != Entry:
                            raise ValueError(
                                "published compatibility first-valid baseline "
                                "changed after validation"
                            )
                    except Exception as Error:
                        PromotionFailure = str(Error)
                    CompatibilityPromotionPassed = (
                        PromotionFailure is None
                    )
                    Comparison["CompatibilityPromotionPassed"] = (
                        CompatibilityPromotionPassed
                    )
                    if CompatibilityCaseName in UnbaselinedCompatibility:
                        UnbaselinedCompatibility.remove(
                            CompatibilityCaseName
                        )
                    Comparison["FirstValidBaselinePromotion"] = {
                        "Attempted": True,
                        "Written": CompatibilityPromotionPassed,
                        "ReferenceAlreadyAvailable": False,
                        "OverwriteBlocked": False,
                        "BaselineAvailableBeforeRun": False,
                        "ReferencePath": str(
                            Configuration.BaselinePath.resolve(
                                strict=False
                            )
                        ),
                        "OriginalReferenceSha256": (
                            BaselineReferenceSha256
                        ),
                        "Failure": PromotionFailure,
                    }
                    if CompatibilityPromotionPassed:
                        Comparison["FirstValidBaselines"] = {
                            **Comparison["FirstValidBaselines"],
                            CompatibilityCase.Name: Entry,
                        }
                    else:
                        Comparison["Passed"] = False
                        Comparison["Failure"] = (
                            "could not persist compatibility first-valid "
                            "baseline: "
                            + (PromotionFailure
                               or "unknown promotion failure")
                        )
                else:
                    Comparison["FirstValidBaselinePromotion"] = {
                        "Attempted": False,
                        "Written": False,
                        "ReferenceAlreadyAvailable": False,
                        "OverwriteBlocked": False,
                        "BaselineAvailableBeforeRun": False,
                    }
            Comparison["UnbaselinedCircuits"] = sorted(
                UnbaselinedCompatibility
            )
        else:
            Comparison["FirstValidBaselinePromotion"] = {
                "Attempted": False,
                "Written": False,
                "ReferenceAlreadyAvailable": (
                    BaselineAvailableBeforeRun
                ),
                "OverwriteBlocked": BaselineAvailableBeforeRun,
                "BaselineAvailableBeforeRun": (
                    BaselineAvailableBeforeRun
                ),
            }
            CompletedRuns.extend({
                **Run,
                "Status": "SKIPPED",
                "Accepted": False,
                "SkipReason": (
                    "extended case is gated behind the complete "
                    "baseline comparison"
                ),
            } for Run in ExtendedPlanned)
        Manifest["Runs"] = CompletedRuns
        Manifest["Accepted"] = (
            bool(Comparison["Passed"])
            and len(CompletedRuns) == len(PlannedRuns)
            and all(bool(Run.get("Accepted")) for Run in CompletedRuns)
        )
        Manifest["Status"] = (
            "PASSED" if Manifest["Accepted"] else "FAILED"
        )
        Manifest["CompletedAtUtc"] = UtcNowProvider()
        WriteManifest(Configuration.ManifestPath, Manifest)
        return Manifest

    CheckSourceProvenance("after-standalone-acceptance")
    Manifest["Runs"] = CompletedRuns
    Manifest["Accepted"] = (
        len(CompletedRuns) == len(PlannedRuns)
        and Manifest.get("SourceProvenanceStable") is True
        and all(bool(Run.get("Accepted")) for Run in CompletedRuns)
    )
    Manifest["Status"] = "PASSED" if Manifest["Accepted"] else "FAILED"
    Manifest["CompletedAtUtc"] = UtcNowProvider()
    WriteManifest(Configuration.ManifestPath, Manifest)
    return Manifest


def DefaultPythonExecutable(RepoRoot: Path) -> Path:
    VirtualEnvironmentPython = RepoRoot / ".venv" / "bin" / "python"
    if VirtualEnvironmentPython.is_file():
        # Keep the venv launcher path itself. Path.resolve() dereferences the
        # common ``.venv/bin/python -> /usr/bin/python`` symlink and launching
        # that resolved target loses the virtual environment's sys.prefix and
        # site-packages selection.
        return VirtualEnvironmentPython.absolute()
    return Path(sys.executable).absolute()


def ParseDateLabel(Value: str) -> str:
    try:
        return date.fromisoformat(Value).isoformat()
    except ValueError as Error:
        raise argparse.ArgumentTypeError(
            "date must use YYYY-MM-DD"
        ) from Error


def BuildParser() -> argparse.ArgumentParser:
    Parser = argparse.ArgumentParser(
        description=(
            "Run the fixed physical router regression matrix sequentially and "
            "write machine-readable accuracy, footprint, and speed evidence."
        )
    )
    Parser.add_argument(
        "--date",
        dest="DateLabel",
        type=ParseDateLabel,
        default=date.today().isoformat(),
        help="dated acceptance directory (default: today)",
    )
    Parser.add_argument(
        "--output-root",
        dest="OutputRoot",
        type=Path,
        default=Path("Output/Regression"),
    )
    Parser.add_argument(
        "--python",
        dest="PythonExecutable",
        type=Path,
        default=None,
        help="compiler Python executable (default: repo .venv or current Python)",
    )
    Parser.add_argument(
        "--routing-threads",
        dest="RoutingThreads",
        type=int,
        default=None,
    )
    BaselineModes = Parser.add_mutually_exclusive_group()
    BaselineModes.add_argument(
        "--capture-baseline",
        dest="CaptureBaseline",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "capture a compact promotable FA/RCA4/RCA8 baseline after "
            "excluded warm-ups"
        ),
    )
    BaselineModes.add_argument(
        "--compare-baseline",
        dest="CompareBaseline",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "compare FA/RCA4/RCA8 against PATH, then run CLA4 only "
            "when --include-cla4 is supplied and all regression gates pass"
        ),
    )
    Parser.add_argument(
        "--expected-policy-version",
        dest="ExpectedPolicyVersion",
        default=None,
        help=(
            "required physical policy version (capture defaults to v15; "
            "comparison defaults to v16)"
        ),
    )
    Parser.add_argument(
        "--capture-timeout-grace-seconds",
        dest="CaptureTimeoutGraceSeconds",
        type=float,
        default=0.0,
        help=(
            "explicit capture-only subprocess grace, from 0 through "
            f"{SubprocessDeadlineGraceSeconds:g} seconds (default: 0)"
        ),
    )
    Parser.add_argument(
        "--include-cla4",
        dest="IncludeCla4",
        action="store_true",
        help=(
            "include the extended CLA4 runs and exact-interface proof "
            "checkpoint; default/no-fallback gates remain enabled"
        ),
    )
    Parser.add_argument(
        "--dry-run",
        dest="DryRun",
        action="store_true",
        help="write the complete sequential plan without launching the compiler",
    )
    return Parser


def Main(Arguments: list[str] | None = None) -> int:
    Parsed = BuildParser().parse_args(Arguments)
    if Parsed.RoutingThreads is not None and Parsed.RoutingThreads <= 0:
        raise SystemExit("--routing-threads must be positive")
    BaselineMode = (
        "capture"
        if Parsed.CaptureBaseline is not None
        else "compare"
        if Parsed.CompareBaseline is not None
        else None
    )
    if (
        not isfinite(Parsed.CaptureTimeoutGraceSeconds)
        or Parsed.CaptureTimeoutGraceSeconds < 0.0
        or Parsed.CaptureTimeoutGraceSeconds
        > SubprocessDeadlineGraceSeconds
    ):
        raise SystemExit(
            "--capture-timeout-grace-seconds must be between zero and "
            f"{SubprocessDeadlineGraceSeconds:g}"
        )
    if (
        Parsed.CaptureTimeoutGraceSeconds > 0.0
        and BaselineMode != "capture"
    ):
        raise SystemExit(
            "--capture-timeout-grace-seconds requires --capture-baseline"
        )
    if Parsed.IncludeCla4 and BaselineMode == "capture":
        raise SystemExit(
            "--include-cla4 cannot be combined with --capture-baseline"
        )
    RoutingThreads = Parsed.RoutingThreads
    if BaselineMode is not None:
        if RoutingThreads is None:
            RoutingThreads = RequiredRegressionRoutingThreads
        elif RoutingThreads != RequiredRegressionRoutingThreads:
            raise SystemExit(
                "baseline capture/compare requires exactly "
                f"{RequiredRegressionRoutingThreads} routing threads"
            )
    RepoRoot = RepositoryRoot.resolve()
    PythonExecutable = (
        Parsed.PythonExecutable.absolute()
        if Parsed.PythonExecutable is not None
        else DefaultPythonExecutable(RepoRoot)
    )
    RequiredVenvPython = (RepoRoot / ".venv" / "bin" / "python").absolute()
    if (
        BaselineMode is not None
        and (
            not RequiredVenvPython.is_file()
            or PythonExecutable != RequiredVenvPython
        )
    ):
        raise SystemExit(
            "baseline capture/compare requires the repository interpreter "
            f"{RequiredVenvPython}"
        )
    OutputRoot = (
        Parsed.OutputRoot
        if Parsed.OutputRoot.is_absolute()
        else RepoRoot / Parsed.OutputRoot
    ).resolve(strict=False)
    RawBaselinePath = (
        Parsed.CaptureBaseline
        if Parsed.CaptureBaseline is not None
        else Parsed.CompareBaseline
    )
    BaselinePath = (
        (
            RawBaselinePath
            if RawBaselinePath.is_absolute()
            else RepoRoot / RawBaselinePath
        ).resolve(strict=False)
        if RawBaselinePath is not None
        else None
    )
    ExpectedPolicyVersion = (
        Parsed.ExpectedPolicyVersion
        if Parsed.ExpectedPolicyVersion is not None
        else (
            BaselinePolicyVersion
            if BaselineMode == "capture"
            else CurrentPolicyVersion
        )
    )
    RequiredModePolicyVersion = {
        "capture": BaselinePolicyVersion,
        "compare": CurrentPolicyVersion,
        None: ExpectedPolicyVersion,
    }[BaselineMode]
    if ExpectedPolicyVersion != RequiredModePolicyVersion:
        raise SystemExit(
            f"{BaselineMode} mode requires --expected-policy-version "
            f"{RequiredModePolicyVersion}"
        )
    Configuration = AcceptanceConfiguration(
        RepositoryRoot=RepoRoot,
        OutputRoot=OutputRoot,
        DateLabel=Parsed.DateLabel,
        PythonExecutable=PythonExecutable,
        DryRun=Parsed.DryRun,
        RoutingThreads=RoutingThreads,
        BaselineMode=BaselineMode,
        BaselinePath=BaselinePath,
        ExpectedPolicyVersion=ExpectedPolicyVersion,
        CaptureTimeoutGraceSeconds=(
            Parsed.CaptureTimeoutGraceSeconds
        ),
        IncludeCla4=Parsed.IncludeCla4,
    )
    Manifest = RunAcceptance(Configuration)
    print(f"Acceptance manifest: {Configuration.ManifestPath}")
    print(f"Acceptance status: {Manifest['Status']}")
    if Configuration.DryRun:
        return 0
    return 0 if Manifest["Accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(Main())
