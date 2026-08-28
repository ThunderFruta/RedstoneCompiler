"""Small orchestrator for exact physical-port factor preparation."""

from __future__ import annotations

from ..Contracts.Component import ComponentCutAccessFeasibilityCertificate
from ..Contracts.PhysicalInterface import PreparedPhysicalComponentPortFactorDomain
from ..Contracts.Results import RoutingResources
from ..ResourceGraph import LocalRouteClaim
from typing import Any
from typing import Callable
from typing import Iterable
from typing import Mapping
from .PortPreparationState import PortPreparationState
from .PortPreparationInputs import (
    ValidatePhysicalPortPreparation,
    BuildPhysicalPortChannelReservations,
    BuildPhysicalPortExteriorFabrics,
    PreparePhysicalPortConnectorSearch,
)
from .PortPreparationFactors import (
    BuildPhysicalPortLaneFactors,
    CertifyPhysicalPortFactors,
    CachePhysicalPortLocalFactors,
    FinalizePhysicalPortPreparation,
)


def PreparePhysicalComponentPortFactorDomain(Placed: Any, Problem: Any, CoarsePlan: Any, Resources: RoutingResources, *, LayerCount: int | None=None, AccessCertificate: ComponentCutAccessFeasibilityCertificate | None=None, AuthoritativeRegion: Any | None=None, AuthoritativeRegionFingerprint: str='', Profiles: Mapping[str, Any] | None=None, FrozenComponentClaims: Iterable[LocalRouteClaim]=(), TechnologyFingerprint: str='', WorkCheck: Callable[[dict[str, object]], None] | None=None) -> PreparedPhysicalComponentPortFactorDomain:
    """Prepare and freeze the complete pre-assignment port factor domain."""
    Context = PortPreparationState(Placed=Placed, Problem=Problem, CoarsePlan=CoarsePlan, Resources=Resources, LayerCount=LayerCount, AccessCertificate=AccessCertificate, AuthoritativeRegion=AuthoritativeRegion, AuthoritativeRegionFingerprint=AuthoritativeRegionFingerprint, Profiles=Profiles, FrozenComponentClaims=FrozenComponentClaims, TechnologyFingerprint=TechnologyFingerprint, WorkCheck=WorkCheck)
    ValidatePhysicalPortPreparation(Context)
    BuildPhysicalPortChannelReservations(Context)
    BuildPhysicalPortExteriorFabrics(Context)
    PreparePhysicalPortConnectorSearch(Context)
    BuildPhysicalPortLaneFactors(Context)
    CertifyPhysicalPortFactors(Context)
    CachePhysicalPortLocalFactors(Context)
    return FinalizePhysicalPortPreparation(Context)
