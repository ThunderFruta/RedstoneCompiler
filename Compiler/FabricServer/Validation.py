"""Local process supervision and vector policy for Fabric validation."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import itertools
import json
import os
from pathlib import Path
import socket
import subprocess
from time import monotonic, sleep
from typing import Any, Callable, Iterable

from .Fixture import FabricFixtureArtifact
from .FailureTrace import BuildFabricFailureTrace
from .Models import (
    FabricServerControlResult,
    FabricServerLoadResult,
    FabricServerValidationResult,
    FabricValidationProgress,
)
from Compiler.Synthesis.LogicEvaluation import EvaluateLogicModule


ExhaustiveInputLimit = 16
WideInputSampleCount = 4096


def DefaultFabricServerRoot() -> Path:
    """Return the repository-owned canonical local Fabric runtime directory."""
    return (
        Path(__file__).resolve().parents[2]
        / "ValidationServerHarness"
        / "Server"
    )


def HasFabricServerRuntime(Root: Path) -> bool:
    """Return whether one runtime root has the launch and harness artifacts."""
    return (
        (Root / "fabric-server-launch.jar").is_file()
        and (Root / "mods" / "redstonecompiler-harness.jar").is_file()
    )


def FindSharedWorktreeFabricServerRoot(
    RepositoryRoot: Path,
    LocalRoot: Path,
) -> Path | None:
    """Find a sibling worktree runtime when this linked checkout has none."""
    if not (RepositoryRoot / ".git").is_file():
        return None
    try:
        Result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=RepositoryRoot,
            capture_output=True,
            check=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for Line in Result.stdout.splitlines():
        if not Line.startswith("worktree "):
            continue
        Candidate = (
            Path(Line.removeprefix("worktree ")).resolve()
            / "ValidationServerHarness"
            / "Server"
        )
        if Candidate != LocalRoot and HasFabricServerRuntime(Candidate):
            return Candidate
    return None


def ResolveFabricServerRoot() -> Path:
    """Return an explicit, local, or shared-worktree Fabric runtime root."""
    ConfiguredRoot = os.environ.get("RC_FABRIC_SERVER_ROOT")
    if ConfiguredRoot:
        return Path(ConfiguredRoot).expanduser().resolve()
    LocalRoot = DefaultFabricServerRoot()
    if HasFabricServerRuntime(LocalRoot):
        return LocalRoot
    RepositoryRoot = Path(__file__).resolve().parents[2]
    return FindSharedWorktreeFabricServerRoot(
        RepositoryRoot,
        LocalRoot,
    ) or LocalRoot


@dataclass(frozen=True)
class FabricServerConfiguration:
    Root: Path | None
    JavaExecutable: str = "java"
    StartupTimeoutSeconds: float = 90.0
    ValidationTimeoutSeconds: float = 900.0
    Port: int = 25566

    @classmethod
    def FromEnvironment(cls) -> "FabricServerConfiguration":
        return cls(
            Root=ResolveFabricServerRoot(),
            JavaExecutable=os.environ.get("RC_FABRIC_JAVA", "java"),
            StartupTimeoutSeconds=float(os.environ.get("RC_FABRIC_STARTUP_TIMEOUT", "90")),
            ValidationTimeoutSeconds=float(
                os.environ.get("RC_FABRIC_VALIDATION_TIMEOUT", "900")
            ),
            Port=int(os.environ.get("RC_FABRIC_CONTROL_PORT", "25566")),
        )


def BuildValidationVectors(InputNames: Iterable[str]) -> list[dict[str, bool]]:
    """Return the committed exhaustive-or-deterministic-wide vector policy."""
    Names = tuple(sorted(str(Name) for Name in InputNames))
    if len(Names) <= ExhaustiveInputLimit:
        return [
            dict(zip(Names, Values))
            for Values in itertools.product((False, True), repeat=len(Names))
        ]
    Values = {
        tuple(False for _ in Names),
        tuple(True for _ in Names),
    }
    for Index in range(len(Names)):
        Values.add(tuple(Index == Other for Other in range(len(Names))))
        Values.add(tuple(Index != Other for Other in range(len(Names))))
    Counter = 0
    while len(Values) < 2 + 2 * len(Names) + WideInputSampleCount:
        Digest = sha256(f"{Names}:{Counter}".encode("utf-8")).digest()
        Values.add(tuple(
            bool(Digest[Index // 8] & (1 << (Index % 8)))
            for Index in range(len(Names))
        ))
        Counter += 1
    return [dict(zip(Names, Value)) for Value in sorted(Values)]


def BuildExpectedVectors(
    Module: Any,
    InputNames: Iterable[str],
    OutputNames: Iterable[str],
    *,
    IncludeTraceValues: bool = False,
) -> list[dict[str, object]]:
    """Pair each requested input assignment with semantic-oracle output bits."""
    InputNames = tuple(str(Name) for Name in InputNames)
    OutputNames = tuple(str(Name) for Name in OutputNames)
    Vectors = []
    for Assignment in BuildValidationVectors(InputNames):
        Values = EvaluateLogicModule(Module, Assignment)
        Vector = {
            "Inputs": Assignment,
            "Expected": {
                Name: bool(Values[Name])
                for Name in OutputNames
            },
        }
        if IncludeTraceValues:
            Vector["ExpectedSignals"] = {
                str(Name): bool(Value)
                for Name, Value in sorted(Values.items())
            }
        Vectors.append(Vector)
    return Vectors


class FabricServerSupervisor:
    """Load or validate circuits through the managed Fabric runtime."""

    def __init__(self, Configuration: FabricServerConfiguration) -> None:
        self.Configuration = Configuration

    def Validate(
        self,
        *,
        Fixture: FabricFixtureArtifact,
        Vectors: list[dict[str, object]],
        ProgressCallback: (
            Callable[[FabricValidationProgress], None] | None
        ) = None,
    ) -> FabricServerValidationResult:
        StartedAt = monotonic()
        Root = self.Configuration.Root
        if Root is None:
            return self._Failure("server-root-not-configured", StartedAt)
        Launcher = Root / "fabric-server-launch.jar"
        Harness = Root / "mods" / "redstonecompiler-harness.jar"
        BuiltHarness = (
            Path(__file__).resolve().parents[2]
            / "ValidationServerHarness"
            / "build"
            / "libs"
            / "validation-server-harness-1.0.0.jar"
        )
        HarnessAvailable = Harness.is_file() or BuiltHarness.is_file()
        if not Launcher.is_file() or not HarnessAvailable:
            return self._Failure("fabric-server-or-harness-not-installed", StartedAt, {
                "ServerRoot": str(Root),
                "LauncherExists": Launcher.is_file(),
                "HarnessExists": HarnessAvailable,
            })
        try:
            RunningControl = self._GetRunningControl(Root)
            if RunningControl is None:
                raise RuntimeError(
                    "Fabric validation requires an authenticated running control endpoint",
                )
            Token, Port = RunningControl
            Response = self._RequestWhenReady(Token, {
                "Action": "Validate",
                "FixturePath": str(Fixture.Path.resolve()),
                "FixtureSha256": Fixture.Sha256,
                "Vectors": Vectors,
            }, Port=Port, ProgressCallback=ProgressCallback)
        except Exception as Error:
            return self._Failure(
                "server-protocol-failure",
                StartedAt,
                {"Error": str(Error)},
            )
        RuntimeSeconds = monotonic() - StartedAt
        Status = str(Response.get("Status", "infrastructure-failure"))
        if Status not in {"passed", "mismatch", "timeout", "infrastructure-failure"}:
            Status = "infrastructure-failure"
        ResponseDiagnostics = dict(Response.get("Diagnostics", {}))
        if Status in {"mismatch", "timeout"}:
            try:
                FixtureDocument = json.loads(Fixture.Path.read_text(encoding="utf-8"))
                FailureTrace = BuildFabricFailureTrace(
                    FixtureDocument,
                    ResponseDiagnostics,
                )
                if FailureTrace is not None:
                    ResponseDiagnostics["FailureTrace"] = FailureTrace
            except (
                KeyError,
                OSError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as Error:
                ResponseDiagnostics["FailureTraceError"] = str(Error)
        return FabricServerValidationResult(
            Status=Status,
            Backend="fabric-26.2",
            RuntimeSeconds=RuntimeSeconds,
            Diagnostics={
                "WorldStateMode": "fixture-paste",
                "WorldCleared": False,
                **ResponseDiagnostics,
                **(
                    {"ControlError": Response["Error"]}
                    if "Error" in Response
                    else {}
                ),
            },
        )

    def LoadIntoRunningServer(self, *, Fixture: FabricFixtureArtifact) -> FabricServerLoadResult:
        """Load a fixture through an already-running local harness.

        This intentionally does not launch or terminate a server, making it
        suitable for manually importing a schematic into the local world.
        """
        StartedAt = monotonic()
        Root = self.Configuration.Root
        if Root is None:
            return FabricServerLoadResult(
                Status="infrastructure-failure",
                RuntimeSeconds=monotonic() - StartedAt,
                Diagnostics={"Reason": "server-root-not-configured"},
            )
        ConfigurationPath = Root / "config" / "redstonecompiler-harness.json"
        try:
            Configuration = json.loads(ConfigurationPath.read_text(encoding="utf-8"))
            Token = str(Configuration["Token"])
            Port = int(Configuration["Port"])
            Response = self._Request(Token, Port, {
                "Action": "LoadFixture",
                "FixturePath": str(Fixture.Path.resolve()),
                "FixtureSha256": Fixture.Sha256,
            })
        except Exception as Error:
            return FabricServerLoadResult(
                Status="infrastructure-failure",
                RuntimeSeconds=monotonic() - StartedAt,
                Diagnostics={"Reason": "running-server-control-unavailable", "Error": str(Error)},
            )
        Status = str(Response.get("Status", "infrastructure-failure"))
        if Status != "loaded":
            Status = "infrastructure-failure"
        return FabricServerLoadResult(
            Status=Status,
            RuntimeSeconds=monotonic() - StartedAt,
            Diagnostics={
                **dict(Response.get("Diagnostics", {})),
                **({"ControlError": Response["Error"]} if "Error" in Response else {}),
            },
        )

    def ValidateExisting(
        self,
        *,
        Fixture: FabricFixtureArtifact,
        Vectors: list[dict[str, object]],
        ProgressCallback: (
            Callable[[FabricValidationProgress], None] | None
        ) = None,
    ) -> FabricServerValidationResult:
        """Validate the current live blocks without clearing or pasting a fixture."""
        StartedAt = monotonic()
        Root = self.Configuration.Root
        if Root is None:
            return self._Failure("server-root-not-configured", StartedAt, {
                "WorldStateMode": "existing",
                "WorldCleared": False,
                "FixturePasted": False,
            })
        try:
            Configuration = json.loads(
                (Root / "config" / "redstonecompiler-harness.json").read_text(
                    encoding="utf-8",
                ),
            )
            Response = self._RequestWhenReady(
                str(Configuration["Token"]),
                {
                    "Action": "ValidateExisting",
                    "FixturePath": str(Fixture.Path.resolve()),
                    "FixtureSha256": Fixture.Sha256,
                    "Vectors": Vectors,
                },
                Port=int(Configuration["Port"]),
                ProgressCallback=ProgressCallback,
            )
        except Exception as Error:
            return self._Failure(
                "existing-world-validation-failure",
                StartedAt,
                {
                    "WorldStateMode": "existing",
                    "WorldCleared": False,
                    "FixturePasted": False,
                    "Error": str(Error),
                },
            )
        return self._BuildValidationResult(
            Response=Response,
            Fixture=Fixture,
            StartedAt=StartedAt,
            PrefixDiagnostics={
                "WorldStateMode": "existing",
                "WorldCleared": False,
                "FixturePasted": False,
            },
        )

    def ControlRunningServer(
        self,
        *,
        Action: str,
        StepTicks: int | None = None,
        ClearRegions: list[dict[str, object]] | None = None,
        WorldBlocks: list[dict[str, object]] | None = None,
        WorldPositions: list[list[int]] | None = None,
        Command: str | None = None,
    ) -> FabricServerControlResult:
        """Configure, pause, resume, or step the running local server."""
        StartedAt = monotonic()
        Root = self.Configuration.Root
        if Root is None:
            return FabricServerControlResult(
                Status="infrastructure-failure",
                RuntimeSeconds=monotonic() - StartedAt,
                Diagnostics={"Reason": "server-root-not-configured"},
            )
        try:
            Configuration = json.loads((Root / "config" / "redstonecompiler-harness.json").read_text(encoding="utf-8"))
            Request: dict[str, object] = {"Action": Action}
            if StepTicks is not None:
                Request["StepTicks"] = int(StepTicks)
            if ClearRegions is not None:
                Request["ClearRegions"] = ClearRegions
            if WorldBlocks is not None:
                Request["Blocks"] = WorldBlocks
            if WorldPositions is not None:
                Request["Positions"] = WorldPositions
            if Command is not None:
                Request["Command"] = Command
            Response = self._Request(str(Configuration["Token"]), int(Configuration["Port"]), Request)
        except Exception as Error:
            return FabricServerControlResult(
                Status="infrastructure-failure",
                RuntimeSeconds=monotonic() - StartedAt,
                Diagnostics={"Reason": "running-server-control-unavailable", "Error": str(Error)},
            )
        Status = str(Response.get("Status", "infrastructure-failure"))
        if Status not in {
            "configured", "paused", "resumed", "stepped", "cleared",
            "observed", "updated", "command-complete",
        }:
            Status = "infrastructure-failure"
        Diagnostics = dict(Response.get("Diagnostics", {}))
        if "Blocks" in Response:
            Diagnostics["Blocks"] = Response["Blocks"]
        return FabricServerControlResult(
            Status=Status,
            RuntimeSeconds=monotonic() - StartedAt,
            Diagnostics={
                **Diagnostics,
                **({"ControlError": Response["Error"]} if "Error" in Response else {}),
            },
        )

    def _GetRunningControl(self, Root: Path) -> tuple[str, int] | None:
        """Return a reachable manager-created endpoint without changing its state."""
        ConfigurationPath = Root / "config" / "redstonecompiler-harness.json"
        try:
            Configuration = json.loads(ConfigurationPath.read_text(encoding="utf-8"))
            Token = str(Configuration["Token"])
            Port = int(Configuration["Port"])
            Response = self._Request(Token, Port, {
                "Action": "WorldReadBlocks",
                "Positions": [],
            })
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, RuntimeError):
            return None
        Status = str(Response.get("Status", ""))
        if Status != "observed":
            Error = str(Response.get("Error", "")).strip()
            raise RuntimeError(
                "running Fabric control did not acknowledge WorldReadBlocks: "
                f"{Status or 'missing-status'}"
                + (f" ({Error})" if Error else ""),
            )
        return Token, Port

    def _BuildValidationResult(
        self,
        *,
        Response: dict[str, object],
        Fixture: FabricFixtureArtifact,
        StartedAt: float,
        PrefixDiagnostics: dict[str, object] | None = None,
    ) -> FabricServerValidationResult:
        """Normalize one harness validation response and attach failure evidence."""
        RuntimeSeconds = monotonic() - StartedAt
        Status = str(Response.get("Status", "infrastructure-failure"))
        if Status not in {"passed", "mismatch", "timeout", "infrastructure-failure"}:
            Status = "infrastructure-failure"
        ResponseDiagnostics = dict(Response.get("Diagnostics", {}))
        if Status in {"mismatch", "timeout"}:
            try:
                FixtureDocument = json.loads(Fixture.Path.read_text(encoding="utf-8"))
                FailureTrace = BuildFabricFailureTrace(
                    FixtureDocument,
                    ResponseDiagnostics,
                )
                if FailureTrace is not None:
                    ResponseDiagnostics["FailureTrace"] = FailureTrace
            except (
                KeyError,
                OSError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ) as Error:
                ResponseDiagnostics["FailureTraceError"] = str(Error)
        return FabricServerValidationResult(
            Status=Status,
            Backend="fabric-26.2",
            RuntimeSeconds=RuntimeSeconds,
            Diagnostics={
                **(PrefixDiagnostics or {}),
                **ResponseDiagnostics,
                **(
                    {"ControlError": Response["Error"]}
                    if "Error" in Response
                    else {}
                ),
            },
        )

    def _RequestWhenReady(
        self,
        Token: str,
        Request: dict[str, object],
        *,
        Port: int,
        ProgressCallback: (
            Callable[[FabricValidationProgress], None] | None
        ) = None,
    ) -> dict[str, object]:
        Deadline = monotonic() + self.Configuration.StartupTimeoutSeconds
        LastError: OSError | None = None
        while monotonic() < Deadline:
            try:
                with socket.create_connection(("127.0.0.1", Port), timeout=2) as Connection:
                    Stream = Connection.makefile("rwb")
                    Payload = {"Token": Token, **Request}
                    Stream.write(json.dumps(Payload, sort_keys=True).encode("utf-8") + b"\n")
                    Stream.flush()
                    ResponseDeadline = (
                        monotonic()
                        + self.Configuration.ValidationTimeoutSeconds
                    )
                    Connection.settimeout(
                        self.Configuration.ValidationTimeoutSeconds,
                    )
                    while True:
                        Response = Stream.readline()
                        if not Response:
                            raise RuntimeError(
                                "harness closed the control connection",
                            )
                        Parsed = json.loads(Response.decode("utf-8"))
                        if Parsed.get("Status") == "progress":
                            Completed = Parsed.get("Completed")
                            Total = Parsed.get("Total")
                            Stage = Parsed.get("Stage")
                            if (
                                type(Completed) is not int
                                or type(Total) is not int
                                or Completed < 0
                                or Total < 0
                                or Completed > Total
                                or not isinstance(Stage, str)
                                or not Stage
                            ):
                                raise RuntimeError(
                                    "harness returned invalid validation progress: "
                                    + json.dumps(Parsed, sort_keys=True),
                                )
                            if ProgressCallback is not None:
                                ProgressCallback(FabricValidationProgress(
                                    Completed=Completed,
                                    Total=Total,
                                    Stage=Stage,
                                    Backend="fabric-26.2-canary",
                                ))
                            RemainingSeconds = ResponseDeadline - monotonic()
                            if RemainingSeconds <= 0.0:
                                raise socket.timeout(
                                    "validation response deadline",
                                )
                            Connection.settimeout(RemainingSeconds)
                            continue
                        if Parsed.get("Error") == "minecraft-server-not-ready":
                            sleep(0.2)
                            break
                        return Parsed
            except socket.timeout as Error:
                raise RuntimeError(
                    "Fabric validation request exceeded its response timeout: "
                    f"{self.Configuration.ValidationTimeoutSeconds:.3f}s",
                ) from Error
            except (OSError, json.JSONDecodeError) as Error:
                LastError = Error
                sleep(0.2)
        raise RuntimeError(f"Fabric harness was not ready: {LastError}")

    @staticmethod
    def _Request(Token: str, Port: int, Request: dict[str, object]) -> dict[str, object]:
        with socket.create_connection(("127.0.0.1", Port), timeout=10) as Connection:
            Stream = Connection.makefile("rwb")
            Stream.write(json.dumps({"Token": Token, **Request}, sort_keys=True).encode("utf-8") + b"\n")
            Stream.flush()
            Response = Stream.readline()
            if not Response:
                raise RuntimeError("harness closed the control connection")
            return json.loads(Response.decode("utf-8"))

    @staticmethod
    def _Failure(Reason: str, StartedAt: float, Extra: dict[str, object] | None = None) -> FabricServerValidationResult:
        return FabricServerValidationResult(
            Status="infrastructure-failure",
            Backend="fabric-26.2",
            RuntimeSeconds=monotonic() - StartedAt,
            Diagnostics={"Reason": Reason, **(Extra or {})},
        )
