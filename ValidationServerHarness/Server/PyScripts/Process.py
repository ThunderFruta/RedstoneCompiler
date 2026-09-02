"""Lifecycle operations for the canonical local Fabric server."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
from time import monotonic, sleep, time

from Anvil import ReadRegionNonAirBlocks
from Paths import (
    BuiltHarnessJarPath,
    EulaPath,
    HarnessConfigurationPath,
    HarnessJarPath,
    LauncherPath,
    ManagerLogPath,
    ManagerStatePath,
    RuntimeRoot,
    ServerPropertiesPath,
    WorldPath,
)
from Protocol import SendRequest, WaitForReady


RequestedTickRate = 1000.0
SettleTimeoutTicks = 200
ServiceUnitName = "redstonecompiler-validation-server.service"
MaximumWorldSetBlocksPerRequest = 10_000

# This only applies when this canonical runtime has no properties yet.  Once a
# server has created its world, its settings remain explicit user-owned state.
NewServerProperties = {
    "allow-flight": "true",
    "allow-nether": "false",
    "difficulty": "peaceful",
    "enable-command-block": "true",
    "enable-query": "false",
    "enable-rcon": "false",
    "force-gamemode": "true",
    "gamemode": "creative",
    "generate-structures": "false",
    "generator-settings": json.dumps({
        "biome": "minecraft:the_void",
        "features": False,
        "lakes": False,
        "layers": [],
        "structure_overrides": [],
    }, separators=(",", ":")),
    "level-name": "world",
    "level-type": "minecraft\\:flat",
    "max-players": "1",
    "motd": "RedstoneCompiler validation simulation",
    "online-mode": "true",
    "pause-when-empty-seconds": "0",
    "pvp": "false",
    "server-ip": "127.0.0.1",
    "server-port": "25565",
    "spawn-animals": "false",
    "spawn-monsters": "false",
    "spawn-npcs": "false",
    "spawn-protection": "0",
}


def ReadManagerState() -> dict[str, object] | None:
    """Return the manager-owned process record when one is available."""
    if not ManagerStatePath.is_file():
        return None
    try:
        State = json.loads(ManagerStatePath.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return State if isinstance(State, dict) else None


def WriteManagerState(State: dict[str, object]) -> None:
    """Atomically persist only the process metadata needed for lifecycle control."""
    TemporaryPath = ManagerStatePath.with_suffix(".tmp")
    TemporaryPath.write_text(
        json.dumps(State, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    TemporaryPath.replace(ManagerStatePath)


def RemoveManagerState() -> None:
    """Remove stale runtime-only manager state."""
    ManagerStatePath.unlink(missing_ok=True)


def ProcessIsOwned(Pid: int) -> bool:
    """Return whether this live PID is the server started by this manager."""
    if Pid <= 0:
        return False
    try:
        os.kill(Pid, 0)
        CommandLine = Path(f"/proc/{Pid}/cmdline").read_bytes().decode(
            "utf-8",
            errors="replace",
        )
    except (OSError, ValueError):
        return False
    return str(LauncherPath) in CommandLine


def FindOwnedServerPid() -> int:
    """Find a canonical runtime JVM when an older manager state was lost."""
    try:
        Candidates = Path("/proc").iterdir()
        for Candidate in Candidates:
            if Candidate.name.isdigit() and ProcessIsOwned(int(Candidate.name)):
                return int(Candidate.name)
    except OSError:
        return 0
    return 0


def CurrentStatus() -> dict[str, object]:
    """Return deterministic manager state without starting or stopping anything."""
    State = ReadManagerState()
    StatePid = int(State.get("Pid", 0)) if State else 0
    Pid = StatePid if ProcessIsOwned(StatePid) else FindOwnedServerPid()
    Running = Pid > 0
    StateLaunchMethod = str(State.get("LaunchMethod", "")) if State else ""
    if Running and (
        State is None
        or StatePid != Pid
        or StateLaunchMethod == "recovered"
    ):
        LaunchMethod = (
            "user-service"
            if ServiceMainPid() == Pid
            else "recovered"
        )
        State = {
            "Launcher": str(LauncherPath),
            "LaunchMethod": LaunchMethod,
            "Pid": Pid,
            "Port": 25566,
            "RecoveredAtUnixSeconds": time(),
        }
        WriteManagerState(State)
    if State and not Running:
        RemoveManagerState()
    return {
        "Status": "running" if Running else "stopped",
        "Pid": Pid if Running else None,
        "ServerRoot": str(RuntimeRoot),
        "ControlPort": int(State.get("Port", 25566)) if Running and State else 25566,
        "LogPath": str(ManagerLogPath),
    }


def EnsureHarnessInstalled() -> None:
    """Refresh the installed harness from the current project build when available."""
    if BuiltHarnessJarPath.is_file() and (
        not HarnessJarPath.is_file()
        or BuiltHarnessJarPath.stat().st_mtime_ns > HarnessJarPath.stat().st_mtime_ns
    ):
        HarnessJarPath.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BuiltHarnessJarPath, HarnessJarPath)
    if not HarnessJarPath.is_file():
        raise RuntimeError(
            "harness JAR is missing; build ValidationServerHarness before starting"
        )


def HarnessNeedsRefresh() -> bool:
    """Return whether a project build needs deployment before the next boot."""
    return BuiltHarnessJarPath.is_file() and (
        not HarnessJarPath.is_file()
        or BuiltHarnessJarPath.stat().st_mtime_ns > HarnessJarPath.stat().st_mtime_ns
    )


def RunningHarnessReady() -> tuple[bool, str | None]:
    """Check the active manager endpoint without modifying world state."""
    try:
        WaitForReady(5.0)
    except (OSError, RuntimeError, ValueError) as Error:
        return False, str(Error)
    return True, None


def EnsureNewServerProperties() -> None:
    """Create the first-boot, terrain-free local simulation world settings."""
    if ServerPropertiesPath.is_file():
        return
    RuntimeRoot.mkdir(parents=True, exist_ok=True)
    ServerPropertiesPath.write_text(
        "".join(
            f"{Key}={Value}\n" for Key, Value in NewServerProperties.items()
        ),
        encoding="utf-8",
    )


def EnsureServerPrerequisites() -> None:
    """Reject a launch that lacks the local-only server prerequisites."""
    if not LauncherPath.is_file():
        raise RuntimeError(f"Fabric launcher is missing: {LauncherPath}")
    EnsureNewServerProperties()
    if not EulaPath.is_file() or "eula=true" not in EulaPath.read_text(
        encoding="utf-8",
    ).lower():
        raise RuntimeError("Minecraft EULA is not accepted in eula.txt")
    EnsureHarnessInstalled()


def WriteHarnessConfiguration(Port: int) -> None:
    """Create one fresh private loopback capability for this server run."""
    if not 1 <= Port <= 65535:
        raise ValueError("control port must be between 1 and 65535")
    HarnessConfigurationPath.parent.mkdir(parents=True, exist_ok=True)
    TemporaryPath = HarnessConfigurationPath.with_suffix(".tmp")
    TemporaryPath.write_text(json.dumps({
        "BindAddress": "127.0.0.1",
        "Port": Port,
        "RequestedTickRate": RequestedTickRate,
        "SettleTimeoutTicks": SettleTimeoutTicks,
        "Token": secrets.token_hex(32),
    }, sort_keys=True), encoding="utf-8")
    TemporaryPath.replace(HarnessConfigurationPath)


def WaitForExit(Pid: int, TimeoutSeconds: float) -> bool:
    """Wait for a manager-owned process to exit."""
    Deadline = monotonic() + TimeoutSeconds
    while monotonic() < Deadline:
        if not ProcessIsOwned(Pid):
            return True
        sleep(0.2)
    return not ProcessIsOwned(Pid)


def UserServiceManagerAvailable() -> bool:
    """Return whether this user session can host a persistent service."""
    if shutil.which("systemctl") is None or shutil.which("systemd-run") is None:
        return False
    try:
        Result = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
        )
    except OSError:
        return False
    return Result.returncode == 0


def ServiceMainPid() -> int:
    """Return the Java PID owned by the persistent service, if it has one."""
    try:
        Result = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                ServiceUnitName,
                "--property=MainPID",
                "--value",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            text=True,
        )
        return int(Result.stdout.strip()) if Result.returncode == 0 else 0
    except (OSError, ValueError):
        return 0


def StartAsUserService(JavaExecutable: str) -> int:
    """Launch Java in a user service that survives the invoking terminal."""
    subprocess.run(
        ["systemctl", "--user", "reset-failed", ServiceUnitName],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        text=True,
    )
    Result = subprocess.run(
        [
            "systemd-run",
            "--user",
            "--collect",
            "--quiet",
            f"--unit={ServiceUnitName}",
            f"--working-directory={RuntimeRoot}",
            f"--property=StandardOutput=append:{ManagerLogPath}",
            f"--property=StandardError=append:{ManagerLogPath}",
            JavaExecutable,
            "-Xms1G",
            "-Xmx2G",
            "-jar",
            str(LauncherPath),
            "nogui",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        text=True,
    )
    if Result.returncode != 0:
        Message = Result.stderr.strip() or Result.stdout.strip() or "unknown error"
        raise RuntimeError(f"could not start Fabric user service: {Message}")
    Deadline = monotonic() + 10.0
    while monotonic() < Deadline:
        Pid = ServiceMainPid()
        if Pid > 0:
            return Pid
        sleep(0.1)
    raise RuntimeError("Fabric user service started without a Java process")


def StopUserService() -> None:
    """Stop the fixed canonical runtime service without touching other units."""
    try:
        Result = subprocess.run(
            ["systemctl", "--user", "stop", ServiceUnitName],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as Error:
        raise RuntimeError(f"could not stop Fabric user service: {Error}") from Error
    if Result.returncode != 0 and "not loaded" not in Result.stderr.lower():
        Message = Result.stderr.strip() or Result.stdout.strip() or "unknown error"
        raise RuntimeError(f"could not stop Fabric user service: {Message}")


def ConfigureSimulationWorld() -> dict[str, object]:
    """Persist the no-spawn, no-drops, frozen-world-time rule set."""
    Response = SendRequest({"Action": "ConfigureQuietWorld"})
    if Response.get("Status") != "configured":
        raise RuntimeError(
            "Fabric harness could not configure the simulation world: "
            + json.dumps(Response, sort_keys=True),
        )
    Diagnostics = Response.get("Diagnostics")
    return Diagnostics if isinstance(Diagnostics, dict) else {}


def StartServer(
    *,
    Port: int = 25566,
    JavaExecutable: str = "java",
    StartupTimeoutSeconds: float = 90.0,
) -> dict[str, object]:
    """Start the canonical server and wait for authenticated control readiness."""
    Status = CurrentStatus()
    RecoveryReasons: list[str] = []
    if Status["Status"] == "running":
        if HarnessNeedsRefresh():
            RecoveryReasons.append("harness-build-updated")
        else:
            Ready, ControlError = RunningHarnessReady()
            if not Ready:
                RecoveryReasons.append(
                    "control-unavailable"
                    + (f": {ControlError}" if ControlError else ""),
                )
        if not RecoveryReasons:
            return {**Status, "Started": False, "Recovered": False}
        StopServer()
    EnsureServerPrerequisites()
    WriteHarnessConfiguration(Port)
    ManagerLogPath.parent.mkdir(parents=True, exist_ok=True)
    if UserServiceManagerAvailable():
        Pid = StartAsUserService(JavaExecutable)
        LaunchMethod = "user-service"
    else:
        with ManagerLogPath.open("a", encoding="utf-8") as Log:
            Process = subprocess.Popen(
                [
                    JavaExecutable,
                    "-Xms1G",
                    "-Xmx2G",
                    "-jar",
                    str(LauncherPath),
                    "nogui",
                ],
                cwd=RuntimeRoot,
                stdin=subprocess.DEVNULL,
                stdout=Log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=True,
            )
        Pid = Process.pid
        LaunchMethod = "process"
    WriteManagerState({
        "JavaExecutable": JavaExecutable,
        "Launcher": str(LauncherPath),
        "LaunchMethod": LaunchMethod,
        "Pid": Pid,
        "Port": Port,
        "StartedAtUnixSeconds": time(),
    })
    try:
        Ready = WaitForReady(StartupTimeoutSeconds)
        WorldConfiguration = ConfigureSimulationWorld()
    except Exception:
        if LaunchMethod == "user-service":
            try:
                StopUserService()
            except RuntimeError:
                pass
        elif ProcessIsOwned(Pid):
            os.killpg(Pid, signal.SIGTERM)
            WaitForExit(Pid, 10.0)
        RemoveManagerState()
        raise
    return {
        **CurrentStatus(),
        "Started": True,
        "Recovered": bool(RecoveryReasons),
        "RecoveryReasons": RecoveryReasons,
        "HarnessStatus": Ready.get("Status"),
        "WorldConfiguration": WorldConfiguration,
    }


def StopServer(*, TimeoutSeconds: float = 30.0) -> dict[str, object]:
    """Gracefully stop the manager-owned server, then fall back to SIGTERM."""
    State = ReadManagerState()
    Pid = int(State.get("Pid", 0)) if State else 0
    LaunchMethod = str(State.get("LaunchMethod", "process")) if State else "process"
    if not ProcessIsOwned(Pid):
        if LaunchMethod == "user-service":
            try:
                StopUserService()
            except RuntimeError:
                pass
        RemoveManagerState()
        return {**CurrentStatus(), "Stopped": False}
    try:
        Response = SendRequest({"Action": "WorldRunCommand", "Command": "stop"})
    except (OSError, RuntimeError, ValueError):
        Response = {}
    if Response.get("Status") == "command-complete":
        Exited = WaitForExit(Pid, TimeoutSeconds)
    else:
        Exited = False
    if not Exited:
        if LaunchMethod == "user-service":
            StopUserService()
        else:
            os.killpg(Pid, signal.SIGTERM)
        if not WaitForExit(Pid, 10.0):
            raise RuntimeError("server did not exit after manager termination")
    RemoveManagerState()
    return {**CurrentStatus(), "Stopped": True}


def DimensionNameForRegionDirectory(RegionDirectory: Path) -> str:
    """Map one canonical world region directory to its Minecraft dimension."""
    try:
        Parts = RegionDirectory.relative_to(WorldPath).parts
    except ValueError as Error:
        raise RuntimeError(
            f"refusing to inspect a region outside the simulation world: {RegionDirectory}",
        ) from Error
    if Parts == ("region",):
        return "minecraft:overworld"
    if Parts == ("DIM-1", "region"):
        return "minecraft:the_nether"
    if Parts == ("DIM1", "region"):
        return "minecraft:the_end"
    if len(Parts) >= 4 and Parts[0] == "dimensions" and Parts[-1] == "region":
        Namespace = Parts[1]
        DimensionPath = "/".join(Parts[2:-1])
        if not Namespace or not DimensionPath:
            raise RuntimeError(f"invalid simulation dimension path: {RegionDirectory}")
        return f"{Namespace}:{DimensionPath}"
    raise RuntimeError(f"unknown simulation region path: {RegionDirectory}")


def PersistedWorldRegionsByDimension() -> dict[str, list[Path]]:
    """Locate every saved block-region file without changing the live world."""
    ResolvedWorldPath = WorldPath.resolve()
    RegionsByDimension: dict[str, list[Path]] = {}
    for RegionPath in sorted(WorldPath.rglob("r.*.*.mca")):
        if RegionPath.parent.name != "region":
            continue
        try:
            RegionPath.resolve().relative_to(ResolvedWorldPath)
        except ValueError as Error:
            raise RuntimeError(
                f"refusing to inspect storage outside the simulation world: {RegionPath}",
            ) from Error
        DimensionName = DimensionNameForRegionDirectory(RegionPath.parent)
        RegionsByDimension.setdefault(DimensionName, []).append(RegionPath)
    return {
        DimensionName: sorted(Regions)
        for DimensionName, Regions in sorted(RegionsByDimension.items())
        if Regions
    }


def ClearPersistedWorldBlocks() -> dict[str, int]:
    """Live-clear only saved non-air blocks through bounded harness requests."""
    RegionsByDimension = PersistedWorldRegionsByDimension()
    NonOverworldDimensions = sorted(
        DimensionName
        for DimensionName in RegionsByDimension
        if DimensionName != "minecraft:overworld"
    )
    if NonOverworldDimensions:
        raise RuntimeError(
            "the active Fabric harness can only clear the simulation overworld; "
            f"saved chunks also exist in {NonOverworldDimensions}",
        )
    RegionPaths = RegionsByDimension.get("minecraft:overworld", [])
    ClearedNonAirBlocks = 0
    ClearedChunkCount = 0
    ScannedChunkCount = 0
    ClearRequestCount = 0
    PendingBlocks: list[dict[str, object]] = []

    def FlushPendingBlocks() -> None:
        """Remove one harness-sized set of positions and require an exact ack."""
        nonlocal ClearRequestCount
        if not PendingBlocks:
            return
        BlocksToClear = list(PendingBlocks)
        Response = SendRequest({
            "Action": "WorldSetBlocks",
            "Blocks": BlocksToClear,
        }, TimeoutSeconds=60.0)
        if Response.get("Status") != "updated":
            raise RuntimeError(
                "Fabric harness could not remove persisted simulation blocks: "
                + json.dumps(Response, sort_keys=True),
            )
        Diagnostics = Response.get("Diagnostics")
        if not isinstance(Diagnostics, dict):
            raise RuntimeError("Fabric harness omitted live world-clear diagnostics")
        UpdatedBlockCount = Diagnostics.get("UpdatedBlockCount")
        if type(UpdatedBlockCount) is not int or UpdatedBlockCount != len(BlocksToClear):
            raise RuntimeError(
                "Fabric harness returned invalid live world-clear diagnostics: "
                + json.dumps(Response, sort_keys=True),
            )
        ClearRequestCount += 1
        PendingBlocks.clear()

    PauseResponse = SendRequest({"Action": "PauseTicks"}, TimeoutSeconds=60.0)
    if PauseResponse.get("Status") != "paused":
        raise RuntimeError(
            "Fabric harness could not pause ticks for a stable world clear: "
            + json.dumps(PauseResponse, sort_keys=True),
        )

    MainError: Exception | None = None
    CleanupErrors: list[str] = []
    SavingDisabled = False
    try:
        SaveOffResponse = SendRequest({
            "Action": "WorldRunCommand",
            "Command": "save-off",
        }, TimeoutSeconds=60.0)
        if SaveOffResponse.get("Status") != "command-complete":
            raise RuntimeError(
                "Fabric harness could not disable saving for a stable world clear: "
                + json.dumps(SaveOffResponse, sort_keys=True),
            )
        SavingDisabled = True

        SaveResponse = SendRequest({
            "Action": "WorldRunCommand",
            "Command": "save-all flush",
        }, TimeoutSeconds=60.0)
        if SaveResponse.get("Status") != "command-complete":
            raise RuntimeError(
                "Fabric harness could not flush live simulation blocks before clear: "
                + json.dumps(SaveResponse, sort_keys=True),
            )

        for RegionPath in RegionPaths:
            for Chunk in ReadRegionNonAirBlocks(RegionPath):
                ScannedChunkCount += 1
                if Chunk.Positions:
                    ClearedChunkCount += 1
                for Position in Chunk.Positions:
                    PendingBlocks.append({
                        "Position": list(Position),
                        "State": "minecraft:air",
                    })
                    ClearedNonAirBlocks += 1
                    if len(PendingBlocks) == MaximumWorldSetBlocksPerRequest:
                        FlushPendingBlocks()
        FlushPendingBlocks()
    except Exception as Error:
        MainError = Error
    finally:
        if SavingDisabled:
            try:
                SaveOnResponse = SendRequest({
                    "Action": "WorldRunCommand",
                    "Command": "save-on",
                }, TimeoutSeconds=60.0)
                if SaveOnResponse.get("Status") != "command-complete":
                    raise RuntimeError(json.dumps(SaveOnResponse, sort_keys=True))
                PersistResponse = SendRequest({
                    "Action": "WorldRunCommand",
                    "Command": "save-all flush",
                }, TimeoutSeconds=60.0)
                if PersistResponse.get("Status") != "command-complete":
                    raise RuntimeError(json.dumps(PersistResponse, sort_keys=True))
            except Exception as Error:
                CleanupErrors.append(f"restore-saving: {Error}")
        try:
            ResumeResponse = SendRequest(
                {"Action": "ResumeTicks"},
                TimeoutSeconds=60.0,
            )
            if ResumeResponse.get("Status") != "resumed":
                raise RuntimeError(json.dumps(ResumeResponse, sort_keys=True))
        except Exception as Error:
            CleanupErrors.append(f"resume-ticks: {Error}")

    if MainError is not None:
        for CleanupError in CleanupErrors:
            MainError.add_note(CleanupError)
        raise MainError
    if CleanupErrors:
        raise RuntimeError(
            "world clear cleanup failed: " + "; ".join(CleanupErrors),
        )
    return {
        "ClearedChunkCount": ClearedChunkCount,
        "ClearedDimensionCount": 1 if RegionPaths else 0,
        "ClearedNonAirBlocks": ClearedNonAirBlocks,
        "ClearRequestCount": ClearRequestCount,
        "ScannedChunkCount": ScannedChunkCount,
        "ScannedRegionFileCount": len(RegionPaths),
        "TicksPausedDuringClear": True,
        "WorldSavingSuppressedDuringScan": True,
        "ClearedStateFlushed": True,
    }


def ClearServerWorld(*, StartupTimeoutSeconds: float = 90.0) -> dict[str, object]:
    """Erase every persisted non-air block without restarting a healthy server."""
    ExpectedWorldPath = RuntimeRoot / "world"
    if WorldPath != ExpectedWorldPath or WorldPath.parent != RuntimeRoot:
        raise RuntimeError(f"refusing to clear unexpected world path: {WorldPath}")
    if WorldPath.is_symlink() or not WorldPath.is_dir():
        raise RuntimeError(f"refusing to create or replace world path: {WorldPath}")

    Status = CurrentStatus()
    if Status["Status"] == "running":
        Ready, ControlError = RunningHarnessReady()
        if not Ready:
            raise RuntimeError(
                "the running Fabric server is not ready for a live block clear: "
                f"{ControlError}",
            )
        Started = {
            **Status,
            "Started": False,
            "Recovered": False,
            "RecoveryReasons": [],
            "HarnessStatus": "observed",
        }
    else:
        Started = StartServer(StartupTimeoutSeconds=StartupTimeoutSeconds)
    ClearDiagnostics = ClearPersistedWorldBlocks()
    return {
        **Started,
        "Cleared": True,
        "ClearMode": "live-persisted-overworld-blocks",
        "WorldPath": str(WorldPath),
        **ClearDiagnostics,
        "Restarted": bool(Started.get("Recovered", False)),
    }


def RestartServer(
    *,
    Port: int = 25566,
    JavaExecutable: str = "java",
    StartupTimeoutSeconds: float = 90.0,
) -> dict[str, object]:
    """Stop a managed server and bring it back with a fresh control token."""
    StopServer()
    return StartServer(
        Port=Port,
        JavaExecutable=JavaExecutable,
        StartupTimeoutSeconds=StartupTimeoutSeconds,
    )


def ReadLogLines(LineCount: int) -> list[str]:
    """Return the requested tail of the manager-owned server log."""
    if LineCount <= 0:
        raise ValueError("log line count must be positive")
    if not ManagerLogPath.is_file():
        return []
    return ManagerLogPath.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines()[-LineCount:]
