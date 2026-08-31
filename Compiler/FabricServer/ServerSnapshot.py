"""Capture an imported fixture from the authoritative Fabric world state."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Iterator

from SchemEncoder.SchemWriter import WriteObservedLitematic

from .SchemImport import ReadLitematicIoLabels
from .Validation import FabricServerSupervisor


WorldReadBatchSize = 10_000
AirBlockNames = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
SnapshotSettleTicks = 50
SnapshotStableReadPasses = 2
SnapshotSettleTimeoutSeconds = 5.0
SnapshotPollSeconds = 0.005


@dataclass(frozen=True)
class FabricServerSnapshotArtifact:
    """Evidence for one litematic captured after a Fabric fixture update."""

    Path: Path
    RequestedPositionCount: int
    ObservedBlockCount: int
    WorldReadRequests: int
    InputCountSetToZero: int
    SnapshotReadPasses: int
    InputZeroGameTime: int | None
    FirstObservedGameTime: int | None
    LastObservedGameTime: int | None


def ReadPosition(Value: object, Name: str) -> tuple[int, int, int]:
    if (
        not isinstance(Value, (list, tuple))
        or len(Value) != 3
        or not all(isinstance(Axis, int) for Axis in Value)
    ):
        raise ValueError(f"Fabric fixture {Name} must be three integer coordinates")
    return tuple(Value)


def ReadFixtureArena(Fixture: dict[str, object]) -> dict[str, object]:
    Arena = Fixture.get("Arena")
    if not isinstance(Arena, dict):
        raise ValueError("Fabric fixture has no Arena compound")
    return Arena


def ReadFixtureOrigin(Fixture: dict[str, object]) -> tuple[int, int, int]:
    """Return the absolute world position of local fixture coordinate zero."""
    return ReadPosition(ReadFixtureArena(Fixture).get("Origin"), "Arena.Origin")


def ReadFixtureBounds(
    Fixture: dict[str, object],
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Return the complete local volume that must be read back from Fabric."""
    Arena = ReadFixtureArena(Fixture)
    RawBounds = Arena.get("Bounds")
    if RawBounds is not None:
        if not isinstance(RawBounds, dict):
            raise ValueError("Fabric fixture Arena.Bounds must be a compound")
        Minimum = ReadPosition(RawBounds.get("Minimum"), "Arena.Bounds.Minimum")
        Maximum = ReadPosition(RawBounds.get("Maximum"), "Arena.Bounds.Maximum")
    else:
        RawBlocks = Fixture.get("Blocks")
        if not isinstance(RawBlocks, list) or not RawBlocks:
            raise ValueError("Fabric fixture without Arena.Bounds must contain blocks")
        Positions = []
        for Index, RawBlock in enumerate(RawBlocks):
            if not isinstance(RawBlock, dict):
                raise ValueError(f"Fabric fixture Blocks[{Index}] is not a compound")
            Positions.append(ReadPosition(RawBlock.get("Position"), f"Blocks[{Index}].Position"))
        Minimum = tuple(min(Position[Axis] for Position in Positions) for Axis in range(3))
        Maximum = tuple(max(Position[Axis] for Position in Positions) for Axis in range(3))
    if any(Minimum[Axis] > Maximum[Axis] for Axis in range(3)):
        raise ValueError(f"Fabric fixture bounds are inverted: {Minimum}..{Maximum}")
    return Minimum, Maximum


def IterFixtureWorldPositionBatches(
    Origin: tuple[int, int, int],
    Bounds: tuple[tuple[int, int, int], tuple[int, int, int]],
) -> Iterator[list[list[int]]]:
    """Yield the fixture volume in harness-sized absolute-world batches."""
    Minimum, Maximum = Bounds
    Batch: list[list[int]] = []
    for LocalY in range(Minimum[1], Maximum[1] + 1):
        for LocalZ in range(Minimum[2], Maximum[2] + 1):
            for LocalX in range(Minimum[0], Maximum[0] + 1):
                Batch.append([
                    Origin[0] + LocalX,
                    Origin[1] + LocalY,
                    Origin[2] + LocalZ,
                ])
                if len(Batch) == WorldReadBatchSize:
                    yield Batch
                    Batch = []
    if Batch:
        yield Batch


def ReadObservedState(Value: object, Position: tuple[int, int, int]) -> dict[str, Any]:
    if not isinstance(Value, dict):
        raise ValueError(f"Fabric world block {Position} has no State compound")
    Name = Value.get("Name")
    if not isinstance(Name, str) or not Name:
        raise ValueError(f"Fabric world block {Position} has an invalid State.Name")
    RawProperties = Value.get("Properties", {})
    if not isinstance(RawProperties, dict) or not all(
        isinstance(Key, str) and isinstance(PropertyValue, str)
        for Key, PropertyValue in RawProperties.items()
    ):
        raise ValueError(f"Fabric world block {Position} has invalid State.Properties")
    State: dict[str, Any] = {"Name": Name}
    if RawProperties:
        State["Properties"] = dict(sorted(RawProperties.items()))
    return State


def EncodeBlockState(State: dict[str, Any]) -> str:
    """Encode one observed state in the harness's WorldSetBlocks syntax."""
    Name = State["Name"]
    Properties = State.get("Properties", {})
    if not Properties:
        return Name
    return Name + "[" + ",".join(
        f"{Key}={Value}"
        for Key, Value in sorted(Properties.items())
    ) + "]"


def ReadFixtureInputs(
    Fixture: dict[str, object],
) -> list[tuple[str, tuple[int, int, int]]]:
    """Return every fixture input name and local lever position exactly once."""
    RawInputs = Fixture.get("Inputs")
    if not isinstance(RawInputs, list):
        raise ValueError("Fabric fixture has no Inputs list")
    Inputs: list[tuple[str, tuple[int, int, int]]] = []
    Names: set[str] = set()
    Positions: set[tuple[int, int, int]] = set()
    for Index, RawInput in enumerate(RawInputs):
        if not isinstance(RawInput, dict):
            raise ValueError(f"Fabric fixture Inputs[{Index}] is not a compound")
        Name = RawInput.get("Name")
        if not isinstance(Name, str) or not Name:
            raise ValueError(f"Fabric fixture Inputs[{Index}] has an invalid Name")
        Position = ReadPosition(
            RawInput.get("LeverPosition"),
            f"Inputs[{Index}].LeverPosition",
        )
        if Name in Names:
            raise ValueError(f"Fabric fixture has duplicate input {Name!r}")
        if Position in Positions:
            raise ValueError(
                f"Fabric fixture inputs share lever position {Position}",
            )
        Names.add(Name)
        Positions.add(Position)
        Inputs.append((Name, Position))
    return Inputs


def ReadExactWorldBlocks(
    Supervisor: FabricServerSupervisor,
    WorldPositions: list[list[int]],
) -> tuple[dict[tuple[int, int, int], dict[str, Any]], int | None]:
    """Read a bounded set of world positions and fail on a partial response."""
    Result = Supervisor.ControlRunningServer(
        Action="WorldReadBlocks",
        WorldPositions=WorldPositions,
    )
    if Result.Status != "observed":
        Detail = Result.Diagnostics.get("Error") or Result.Diagnostics.get("Reason")
        raise RuntimeError(
            "Fabric server did not provide an observed world snapshot"
            + (f": {Detail}" if Detail else ""),
        )
    RawBlocks = Result.Diagnostics.get("Blocks")
    if not isinstance(RawBlocks, list):
        raise RuntimeError("Fabric server observation omitted Blocks")
    ExpectedPositions = {tuple(Position) for Position in WorldPositions}
    Observed: dict[tuple[int, int, int], dict[str, Any]] = {}
    for RawBlock in RawBlocks:
        if not isinstance(RawBlock, dict):
            raise RuntimeError("Fabric server observation contains a non-compound block")
        WorldPosition = ReadPosition(RawBlock.get("Position"), "world block Position")
        if WorldPosition not in ExpectedPositions:
            raise RuntimeError(
                f"Fabric server returned unexpected world block {WorldPosition}",
            )
        if WorldPosition in Observed:
            raise RuntimeError(
                f"Fabric server returned duplicate world block {WorldPosition}",
            )
        Observed[WorldPosition] = ReadObservedState(
            RawBlock.get("State"),
            WorldPosition,
        )
    MissingPositions = ExpectedPositions - set(Observed)
    if MissingPositions:
        raise RuntimeError(
            "Fabric server observation omitted requested world blocks: "
            + repr(sorted(MissingPositions)[:8]),
        )
    GameTime = Result.Diagnostics.get("ObservedGameTime")
    return Observed, GameTime if isinstance(GameTime, int) else None


def SetFixtureInputsToZero(
    Supervisor: FabricServerSupervisor,
    Origin: tuple[int, int, int],
    Inputs: list[tuple[str, tuple[int, int, int]]],
) -> tuple[int, int | None, int]:
    """Use the live server state to force every fixture lever to powered=false."""
    if not Inputs:
        return 0, None, 0
    WorldPositions = [
        [
            Origin[0] + Position[0],
            Origin[1] + Position[1],
            Origin[2] + Position[2],
        ]
        for _Name, Position in Inputs
    ]
    ExistingStates, _ReadGameTime = ReadExactWorldBlocks(Supervisor, WorldPositions)
    WorldBlocks = []
    for Name, LocalPosition in Inputs:
        WorldPosition = tuple(
            Origin[Axis] + LocalPosition[Axis]
            for Axis in range(3)
        )
        State = ExistingStates[WorldPosition]
        if State["Name"] != "minecraft:lever":
            raise RuntimeError(
                f"Fabric fixture input {Name!r} is not a lever at {WorldPosition}: "
                f"{State['Name']}",
            )
        Properties = dict(State.get("Properties", {}))
        Properties["powered"] = "false"
        WorldBlocks.append({
            "Position": list(WorldPosition),
            "State": EncodeBlockState({
                "Name": State["Name"],
                "Properties": dict(sorted(Properties.items())),
            }),
        })
    Result = Supervisor.ControlRunningServer(
        Action="WorldSetBlocks",
        WorldBlocks=WorldBlocks,
    )
    if Result.Status != "updated":
        Detail = Result.Diagnostics.get("Error") or Result.Diagnostics.get("Reason")
        raise RuntimeError(
            "Fabric server did not acknowledge resetting fixture inputs to zero"
            + (f": {Detail}" if Detail else ""),
        )
    GameTime = Result.Diagnostics.get("ObservedGameTime")
    return len(WorldBlocks), GameTime if isinstance(GameTime, int) else None, 1


def ReadFixtureWorldState(
    Supervisor: FabricServerSupervisor,
    Origin: tuple[int, int, int],
    Bounds: tuple[tuple[int, int, int], tuple[int, int, int]],
) -> tuple[dict[tuple[int, int, int], dict[str, Any]], int, list[int]]:
    """Read one complete fixture volume as local non-air block states."""
    ObservedBlocks: dict[tuple[int, int, int], dict[str, Any]] = {}
    ObservedGameTimes: list[int] = []
    WorldReadRequests = 0
    for WorldPositions in IterFixtureWorldPositionBatches(Origin, Bounds):
        Observed, GameTime = ReadExactWorldBlocks(Supervisor, WorldPositions)
        for WorldPosition, State in Observed.items():
            LocalPosition = tuple(
                WorldPosition[Axis] - Origin[Axis]
                for Axis in range(3)
            )
            if State["Name"] not in AirBlockNames:
                ObservedBlocks[LocalPosition] = State
        WorldReadRequests += 1
        if GameTime is not None:
            ObservedGameTimes.append(GameTime)
    return ObservedBlocks, WorldReadRequests, ObservedGameTimes


def WaitForSettledFixtureWorld(
    Supervisor: FabricServerSupervisor,
    Origin: tuple[int, int, int],
    Bounds: tuple[tuple[int, int, int], tuple[int, int, int]],
    InputZeroGameTime: int | None,
) -> tuple[dict[tuple[int, int, int], dict[str, Any]], int, int, list[int]]:
    """Require a post-input-zero fixture state stable across consecutive reads."""
    Deadline = monotonic() + SnapshotSettleTimeoutSeconds
    PreviousBlocks: dict[tuple[int, int, int], dict[str, Any]] | None = None
    StableReadPasses = 0
    SnapshotReadPasses = 0
    WorldReadRequests = 0
    ObservedGameTimes: list[int] = []
    while True:
        CurrentBlocks, CurrentRequests, CurrentGameTimes = ReadFixtureWorldState(
            Supervisor,
            Origin,
            Bounds,
        )
        SnapshotReadPasses += 1
        WorldReadRequests += CurrentRequests
        ObservedGameTimes.extend(CurrentGameTimes)
        LastGameTime = CurrentGameTimes[-1] if CurrentGameTimes else None
        ReachedTickBudget = (
            InputZeroGameTime is None
            or LastGameTime is None
            or LastGameTime >= InputZeroGameTime + SnapshotSettleTicks
        )
        if ReachedTickBudget and CurrentBlocks == PreviousBlocks:
            StableReadPasses += 1
        elif ReachedTickBudget:
            StableReadPasses = 1
        else:
            StableReadPasses = 0
        if StableReadPasses >= SnapshotStableReadPasses:
            return (
                CurrentBlocks,
                SnapshotReadPasses,
                WorldReadRequests,
                ObservedGameTimes,
            )
        if monotonic() >= Deadline:
            TargetGameTime = (
                InputZeroGameTime + SnapshotSettleTicks
                if InputZeroGameTime is not None
                else None
            )
            raise RuntimeError(
                "Fabric world did not settle before server snapshot timeout: "
                f"reads={SnapshotReadPasses} "
                f"stableReads={StableReadPasses} "
                f"targetGameTime={TargetGameTime} "
                f"lastGameTime={LastGameTime}",
            )
        PreviousBlocks = CurrentBlocks
        sleep(SnapshotPollSeconds)


def ReadPreservedIoSigns(
    SourcePath: Path,
    Minimum: tuple[int, int, int],
) -> list[tuple[tuple[int, int, int], str]]:
    """Preserve compiler I/O labels in the fixture's local coordinate space.

    ``WriteLitematic`` encodes every block and tile entity relative to the
    rendered design's minimum position.  The Fabric fixture instead retains
    the renderer's original local coordinates, which can be negative when a
    routed design extends around an I/O cell.  Translate the serialized sign
    positions back by that fixture minimum before handing them to
    ``WriteObservedLitematic``.  Imported litematics use a zero minimum, so
    their coordinates remain unchanged.
    """
    if SourcePath.suffix.lower() != ".litematic":
        return []
    return [
        (
            tuple(
                Position[Axis] + Minimum[Axis]
                for Axis in range(3)
            ),
            f"{Prefix} {Name}",
        )
        for Position, Prefix, Name in ReadLitematicIoLabels(SourcePath)
    ]


def CaptureServerUpdatedLitematic(
    *,
    Supervisor: FabricServerSupervisor,
    Fixture: dict[str, object],
    SourcePath: Path,
    OutputPath: Path,
) -> FabricServerSnapshotArtifact:
    """Write a settled server snapshot after explicitly forcing all inputs low.

    The post-load server state is not trusted as an implicit input baseline.
    Every fixture lever is read from Minecraft, updated through
    ``WorldSetBlocks`` with ``powered=false``, then the full fixture volume is
    observed until it has been unchanged across consecutive reads after a
    fifty-game-tick settle budget. The emitted artifact therefore represents
    Minecraft's fully block-updated all-zero-input state.
    """
    Origin = ReadFixtureOrigin(Fixture)
    Bounds = ReadFixtureBounds(Fixture)
    Inputs = ReadFixtureInputs(Fixture)
    InputCountSetToZero, InputZeroGameTime, InputReadRequests = SetFixtureInputsToZero(
        Supervisor,
        Origin,
        Inputs,
    )
    (
        ObservedBlocks,
        SnapshotReadPasses,
        SnapshotReadRequests,
        ObservedGameTimes,
    ) = WaitForSettledFixtureWorld(
        Supervisor,
        Origin,
        Bounds,
        InputZeroGameTime,
    )
    for Name, Position in Inputs:
        State = ObservedBlocks.get(Position)
        if (
            State is None
            or State.get("Name") != "minecraft:lever"
            or State.get("Properties", {}).get("powered") != "false"
        ):
            raise RuntimeError(
                f"Fabric server did not preserve zero input {Name!r} at {Position}",
            )
    Minimum, Maximum = Bounds
    RequestedPositionCount = (
        (Maximum[0] - Minimum[0] + 1)
        * (Maximum[1] - Minimum[1] + 1)
        * (Maximum[2] - Minimum[2] + 1)
    )

    WriteObservedLitematic(
        ObservedBlocks,
        OutputPath,
        Bounds=Bounds,
        Signs=ReadPreservedIoSigns(SourcePath, Minimum),
    )
    return FabricServerSnapshotArtifact(
        Path=Path(OutputPath).resolve(),
        RequestedPositionCount=RequestedPositionCount,
        ObservedBlockCount=len(ObservedBlocks),
        WorldReadRequests=InputReadRequests + SnapshotReadRequests,
        InputCountSetToZero=InputCountSetToZero,
        SnapshotReadPasses=SnapshotReadPasses,
        InputZeroGameTime=InputZeroGameTime,
        FirstObservedGameTime=(ObservedGameTimes[0] if ObservedGameTimes else None),
        LastObservedGameTime=(ObservedGameTimes[-1] if ObservedGameTimes else None),
    )
