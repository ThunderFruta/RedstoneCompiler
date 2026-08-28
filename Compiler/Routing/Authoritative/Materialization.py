"""Final physical component assembly construction."""

from __future__ import annotations

from ..Contracts.Component import ComponentCutAccessFeasibilityCertificate

from ..Contracts.PhysicalInterface import PreparedPhysicalComponentAssembly

from ..Contracts.Results import RoutingResources

from ..ResourceGraph import LocalRouteClaim

from typing import Any

from typing import Callable

from typing import Iterable

from .PortPreparation import (
    PreparePhysicalComponentPortFactorDomain,
)

from .PortSolving import (
    SolvePreparedPhysicalComponentPortFactorDomain,
)

def BuildPhysicalComponentAssemblyPlan(
    Placed: Any,
    Problem: Any,
    CoarsePlan: Any,
    Resources: RoutingResources,
    *,
    LayerCount: int | None = None,
    AccessCertificate: ComponentCutAccessFeasibilityCertificate | None = None,
    WorkCheck: Callable[[dict[str, object]], None] | None = None,
) -> PreparedPhysicalComponentAssembly:
    """Prepare then solve the authoritative physical component port domain."""
    Preparation = PreparePhysicalComponentPortFactorDomain(
        Placed,
        Problem,
        CoarsePlan,
        Resources,
        LayerCount=LayerCount,
        AccessCertificate=AccessCertificate,
        WorkCheck=WorkCheck,
    )
    return SolvePreparedPhysicalComponentPortFactorDomain(
        Preparation,
        Resources,
        WorkCheck=WorkCheck,
    )

def SelectComponentPreparationProfiles(
    Profiles: dict[str, Any],
    ComponentSignals: frozenset[str],
    InterClusterChannel: Any,
    LocalClaims: Iterable[LocalRouteClaim],
    *,
    GuideExpansion: int,
    TrackPitch: int,
) -> dict[str, Any]:
    """Keep owned nets and only passive escapes that can touch the component."""
    InteractionColumns = {
        (int(Cell[0]), int(Cell[2]))
        for Lane in getattr(InterClusterChannel, "Lanes", ())
        for Cell in getattr(Lane, "Cells", ())
    }
    InteractionColumns.update(
        (int(Position[0]), int(Position[2]))
        for Claim in LocalClaims
        if Claim.Signal in ComponentSignals
        for Position in Claim.Nodes
    )
    for Signal in ComponentSignals:
        Profile = Profiles.get(Signal)
        if Profile is None:
            continue
        InteractionColumns.update(
            (int(Position[0]), int(Position[2]))
            for Path in (
                Profile.SourceAccessPath,
                *Profile.TargetAccessPaths.values(),
            )
            for Position in Path
        )
    InteractionRadius = max(
        0,
        # A component egress reaches one layer-pitch plus one track-pitch
        # beyond its deck attachment; the final cell's electrical exclusion
        # adds one more column.
        int(GuideExpansion) + int(TrackPitch) + 3,
    )

    def CanInteract(Profile: Any) -> bool:
        if not InteractionColumns:
            return False
        AccessColumns = {
            (int(Position[0]), int(Position[2]))
            for Path in (
                Profile.SourceAccessPath,
                *Profile.TargetAccessPaths.values(),
            )
            for Position in Path
        }
        return any(
            abs(AccessX - ComponentX)
            + abs(AccessZ - ComponentZ)
            <= InteractionRadius
            for AccessX, AccessZ in AccessColumns
            for ComponentX, ComponentZ in InteractionColumns
        )

    return {
        Signal: Profile
        for Signal, Profile in Profiles.items()
        if Signal in ComponentSignals or CanInteract(Profile)
    }
