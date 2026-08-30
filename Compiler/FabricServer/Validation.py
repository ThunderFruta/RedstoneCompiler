"""Local process supervision and vector policy for Fabric validation."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import itertools
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
from time import monotonic, sleep
from typing import Any, Iterable

from .Fixture import FabricFixtureArtifact
from .Models import (
    FabricServerControlResult,
    FabricServerLoadResult,
    FabricServerValidationResult,
)
from Compiler.Synthesis.LogicEvaluation import EvaluateLogicModule


RequestedTickRate = 1000.0
SettleTimeoutTicks = 200
ExhaustiveInputLimit = 16
WideInputSampleCount = 4096


@dataclass(frozen=True)
class FabricServerConfiguration:
    Root: Path | None
    JavaExecutable: str = "java"
    StartupTimeoutSeconds: float = 90.0
    Port: int = 25566

    @classmethod
    def FromEnvironment(cls) -> "FabricServerConfiguration":
        Root = os.environ.get("RC_FABRIC_SERVER_ROOT")
        return cls(
            Root=Path(Root).expanduser().resolve() if Root else None,
            JavaExecutable=os.environ.get("RC_FABRIC_JAVA", "java"),
            StartupTimeoutSeconds=float(os.environ.get("RC_FABRIC_STARTUP_TIMEOUT", "90")),
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


def BuildExpectedVectors(Module: Any, InputNames: Iterable[str], OutputNames: Iterable[str]) -> list[dict[str, object]]:
    """Pair each requested input assignment with semantic-oracle output bits."""
    return [
        {
            "Inputs": Assignment,
            "Expected": {
                Name: bool(EvaluateLogicModule(Module, Assignment)[Name])
                for Name in OutputNames
            },
        }
        for Assignment in BuildValidationVectors(InputNames)
    ]


class FabricServerSupervisor:
    """Run a private dedicated server and exchange authenticated JSON lines."""

    def __init__(self, Configuration: FabricServerConfiguration) -> None:
        self.Configuration = Configuration

    def Validate(
        self,
        *,
        Fixture: FabricFixtureArtifact,
        Vectors: list[dict[str, object]],
    ) -> FabricServerValidationResult:
        StartedAt = monotonic()
        Root = self.Configuration.Root
        if Root is None:
            return self._Failure("server-root-not-configured", StartedAt)
        Launcher = Root / "fabric-server-launch.jar"
        Harness = Root / "mods" / "redstonecompiler-harness.jar"
        BuiltHarness = (
            Path(__file__).resolve().parents[2]
            / "FabricServerHarness"
            / "build"
            / "libs"
            / "redstonecompiler-harness-1.0.0.jar"
        )
        if BuiltHarness.is_file() and (
            not Harness.is_file()
            or BuiltHarness.stat().st_mtime_ns > Harness.stat().st_mtime_ns
        ):
            Harness.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(BuiltHarness, Harness)
        if not Launcher.is_file() or not Harness.is_file():
            return self._Failure("fabric-server-or-harness-not-installed", StartedAt, {
                "ServerRoot": str(Root),
                "LauncherExists": Launcher.is_file(),
                "HarnessExists": Harness.is_file(),
            })
        Token = secrets.token_hex(32)
        ConfigurationPath = Root / "config" / "redstonecompiler-harness.json"
        ConfigurationPath.parent.mkdir(parents=True, exist_ok=True)
        ConfigurationPath.write_text(json.dumps({
            "BindAddress": "127.0.0.1",
            "Port": self.Configuration.Port,
            "Token": Token,
            "RequestedTickRate": RequestedTickRate,
            "SettleTimeoutTicks": SettleTimeoutTicks,
        }, sort_keys=True), encoding="utf-8")
        Process = subprocess.Popen(
            [self.Configuration.JavaExecutable, "-Xms1G", "-Xmx2G", "-jar", str(Launcher), "nogui"],
            cwd=Root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            Response = self._RequestWhenReady(Token, {
                "Action": "Validate",
                "FixturePath": str(Fixture.Path.resolve()),
                "FixtureSha256": Fixture.Sha256,
                "Vectors": Vectors,
            })
        except Exception as Error:
            return self._Failure("server-protocol-failure", StartedAt, {"Error": str(Error)})
        finally:
            if Process.poll() is None:
                Process.terminate()
                try:
                    Process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    Process.kill()
        RuntimeSeconds = monotonic() - StartedAt
        Status = str(Response.get("Status", "infrastructure-failure"))
        if Status not in {"passed", "mismatch", "timeout", "infrastructure-failure"}:
            Status = "infrastructure-failure"
        return FabricServerValidationResult(
            Status=Status,
            Backend="fabric-26.2",
            RuntimeSeconds=RuntimeSeconds,
            Diagnostics={
                **dict(Response.get("Diagnostics", {})),
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

    def ControlRunningServer(
        self,
        *,
        Action: str,
        StepTicks: int | None = None,
        ClearRegions: list[dict[str, object]] | None = None,
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
            Response = self._Request(str(Configuration["Token"]), int(Configuration["Port"]), Request)
        except Exception as Error:
            return FabricServerControlResult(
                Status="infrastructure-failure",
                RuntimeSeconds=monotonic() - StartedAt,
                Diagnostics={"Reason": "running-server-control-unavailable", "Error": str(Error)},
            )
        Status = str(Response.get("Status", "infrastructure-failure"))
        if Status not in {"configured", "paused", "resumed", "stepped", "cleared"}:
            Status = "infrastructure-failure"
        return FabricServerControlResult(
            Status=Status,
            RuntimeSeconds=monotonic() - StartedAt,
            Diagnostics={
                **dict(Response.get("Diagnostics", {})),
                **({"ControlError": Response["Error"]} if "Error" in Response else {}),
            },
        )

    def _RequestWhenReady(self, Token: str, Request: dict[str, object]) -> dict[str, object]:
        Deadline = monotonic() + self.Configuration.StartupTimeoutSeconds
        LastError: OSError | None = None
        while monotonic() < Deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.Configuration.Port), timeout=2) as Connection:
                    Stream = Connection.makefile("rwb")
                    Payload = {"Token": Token, **Request}
                    Stream.write(json.dumps(Payload, sort_keys=True).encode("utf-8") + b"\n")
                    Stream.flush()
                    Response = Stream.readline()
                    if not Response:
                        raise RuntimeError("harness closed the control connection")
                    Parsed = json.loads(Response.decode("utf-8"))
                    if Parsed.get("Error") == "minecraft-server-not-ready":
                        sleep(0.2)
                        continue
                    return Parsed
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
