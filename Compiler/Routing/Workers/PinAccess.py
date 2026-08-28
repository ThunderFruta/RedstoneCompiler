"""Static pin-access analysis worker."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

try:
    from ...RustRouting import RoutingContext as RustRoutingContext
except ImportError:
    try:
        from RedstoneCompiler.RustRouting import RoutingContext as RustRoutingContext
    except Exception:
        RustRoutingContext = None

from ...Placement.Geometry import GetGateInputAccess
from ...Placement.Rotation import RotatedCellSize
from ..Actions.Geometry import BuildRoutingResources
from ..Contracts.Results import (
    PinAccessIssue,
    PinAccessReport,
)
from ..Contracts.Core import Position3
from ..ResourceGraph import PinAccessSelection
from ..Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)


@dataclass(frozen=True)
class PinAccessWorker:
    """Prove every source-to-sink escape against static placement obstacles."""

    SearchMargin: int = 20
    MaximumRoutingHeight: int = 32
    AccessLength: int = 3
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology

    def Run(self, Placed: Any) -> PinAccessReport:
        if RustRoutingContext is None:
            raise ValueError("Pin-access analysis requires the Rust router")

        Resources = BuildRoutingResources(Placed)
        ActualBlocks = set(Resources.StaticGeometry.ActualBlocks)
        ElectricalBlocks = set(Resources.StaticGeometry.ElectricalBlocks)
        SolidBlocks = set(Resources.StaticGeometry.SolidBlocks)
        Producers = {
            Signal: Gate
            for Gate in Placed.PlacedGates
            if Gate.OutputPin is not None
            for Signal in Gate.Outputs
        }
        Targets: dict[str, list[Position3]] = defaultdict(list)
        AccessBySignal: dict[str, set[Position3]] = defaultdict(set)
        for Gate in Placed.PlacedGates:
            if Gate.OutputPin is not None:
                X, Y, Z = Gate.OutputPin
                DeltaX, DeltaY, DeltaZ = Gate.OutputDirection
                for Signal in Gate.Outputs:
                    AccessBySignal[Signal].update(
                        (
                            X + DeltaX * Offset,
                            Y + DeltaY * Offset,
                            Z + DeltaZ * Offset,
                        )
                        for Offset in range(self.AccessLength)
                    )
            for InputIndex, Signal in enumerate(Gate.Inputs):
                Pin, Direction = GetGateInputAccess(Gate, InputIndex)
                Targets[Signal].append(Pin)
                X, Y, Z = Pin
                DeltaX, DeltaY, DeltaZ = Direction
                AccessBySignal[Signal].update(
                    (
                        X + DeltaX * Offset,
                        Y + DeltaY * Offset,
                        Z + DeltaZ * Offset,
                    )
                    for Offset in range(self.AccessLength)
                )

        ReservedAccess = (
            set().union(*AccessBySignal.values()) if AccessBySignal else set()
        )
        MinimumX = min(Gate.X for Gate in Placed.PlacedGates)
        MaximumX = max(
            Gate.X + RotatedCellSize(Gate.Kind, Gate.Rotation)[0] - 1
            for Gate in Placed.PlacedGates
        )
        MinimumZ = min(Gate.Z for Gate in Placed.PlacedGates)
        MaximumZ = max(
            Gate.Z + RotatedCellSize(Gate.Kind, Gate.Rotation)[1] - 1
            for Gate in Placed.PlacedGates
        )
        MinimumY = min(Gate.Y for Gate in Placed.PlacedGates)
        Bounds = (
            MinimumX - self.SearchMargin,
            MaximumX + self.SearchMargin,
            MinimumY,
            MinimumY + self.MaximumRoutingHeight,
            MinimumZ - self.SearchMargin,
            MaximumZ + self.SearchMargin,
        )
        Region = Resources.ResourceGraph.BuildRegion(
            Bounds,
            AllowedAccess=frozenset(ReservedAccess),
        )
        Context = RustRoutingContext(
            Bounds,
            (MinimumX, MaximumX, MinimumZ, MaximumZ),
            sorted(Region.Nodes),
            sorted(Region.Edges),
        )
        Issues: list[PinAccessIssue] = []
        Selections: list[PinAccessSelection] = []
        CheckedTargets = 0
        for Signal in sorted(Targets):
            Producer = Producers.get(Signal)
            if Producer is None:
                continue
            Root = Producer.OutputPin
            DeltaX, DeltaY, DeltaZ = Producer.OutputDirection
            Starts = [
                (
                    Root[0] + DeltaX * Offset,
                    Root[1] + DeltaY * Offset,
                    Root[2] + DeltaZ * Offset,
                )
                for Offset in range(self.AccessLength)
            ]
            SignalTargets = sorted(set(Targets[Signal]))
            CheckedTargets += len(SignalTargets)
            Paths = Context.FindPathsOnResourceGraph(
                Starts,
                SignalTargets,
                MinimumY + 1,
                [],
                [],
                [],
                6,
                4,
                1,
                200_000,
            )
            for Target, Path in zip(SignalTargets, Paths):
                PhysicallyReachable = False
                if Path is not None:
                    Candidate = set(Starts) | set(Path)
                    EntryConnected = (
                        not Path
                        or any(Region.ContainsEdge(Start, Path[0]) for Start in Starts)
                    )
                    PhysicallyReachable = (
                        Target in Candidate
                        and EntryConnected
                        and all(
                            Region.ContainsEdge(First, Second)
                            for First, Second in zip(Path, Path[1:])
                        )
                    )
                if not PhysicallyReachable:
                    Issues.append(
                        PinAccessIssue(
                            Signal=Signal,
                            Source=Root,
                            Target=Target,
                        )
                    )
                else:
                    SelectedPath = tuple(Starts) + tuple(Path or ())
                    Selections.append(
                        PinAccessSelection(
                            Signal=Signal,
                            Source=Root,
                            Target=Target,
                            Path=SelectedPath,
                            ReservedResources=Resources.ResourceGraph.BuildRouteClaims(
                                SelectedPath
                            ).ResourceIds,
                        )
                    )

        return PinAccessReport(
            CheckedTargets=CheckedTargets,
            Issues=tuple(Issues),
            Selections=tuple(Selections),
        )


def AnalyzePinAccess(
    Placed: Any,
    SearchMargin: int = 20,
    MaximumRoutingHeight: int = 32,
    AccessLength: int = 3,
    Technology: RedstoneRoutingTechnology = DefaultRedstoneRoutingTechnology,
) -> PinAccessReport:
    """Compatibility action for callers that do not retain worker instances."""
    return PinAccessWorker(
        SearchMargin=SearchMargin,
        MaximumRoutingHeight=MaximumRoutingHeight,
        AccessLength=AccessLength,
        Technology=Technology,
    ).Run(Placed)
