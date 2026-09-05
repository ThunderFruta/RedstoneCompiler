"""Dependency-direction checks for the neutral routing contract packages."""

from __future__ import annotations

from PhysicalDesign.Contracts import Component, Core, PhysicalInterface, Placement, Results
ContractFamilies = {
    "Component": Component,
    "Core": Core,
    "PhysicalInterface": PhysicalInterface,
    "Placement": Placement,
    "Results": Results,
}


def test_routing_contract_dependencies_are_one_way() -> None:
    """Neutral contracts cannot import routing or placement orchestrators."""

    ForbiddenPrefixes = (
        "PhysicalDesign.Orchestration",
        "PhysicalDesign.Routing.Global",
        "PhysicalDesign.Routing.Regions",
    )
    for Module in ContractFamilies.values():
        for Value in vars(Module).values():
            ImportedModule = getattr(Value, "__module__", "")
            assert not ImportedModule.startswith(ForbiddenPrefixes)
