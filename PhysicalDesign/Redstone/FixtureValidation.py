"""Expectation-free fixture queries over the existing routing-rule authorities."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from .Technology import (
    DefaultRedstoneRoutingTechnology,
    RepeaterInputDelta,
    RepeaterOutputDelta,
)
from .Rules.Geometry import NonSolidBlockNames
from .Rules.Validation import AnalyzeFlatRouteConflicts, BuildPhysicalGraphs
from .Rules.Repeaters import PropagateRoutePower
from .Rules.Stairs import (
    DustStairDecisionVersion,
    ElectricalBlockNames,
    GeometryStatus,
    QueryDustStair,
    RouteClaimStatus,
)


FixtureCheckerVersion = "physical-fixture-checker-v1"


def FixtureIdentity(Fixture: Mapping[str, Any]) -> str:
    """Bind every normalized fixture field, including controls and probes."""
    return sha256((json.dumps(Fixture, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


def ModelIdentity() -> dict[str, Any]:
    """Bind technology settings and the exact local shared-rule source."""
    Root = Path(__file__).resolve().parent
    Sources = {
        Name: sha256((Root / Name).read_bytes()).hexdigest()
        for Name in ("FixtureValidation.py", "Technology.py", "Rules/Geometry.py", "Rules/Validation.py", "Rules/Repeaters.py", "Rules/Stairs.py")
    }
    Technology = asdict(DefaultRedstoneRoutingTechnology)
    return {
        "Version": FixtureCheckerVersion,
        "TechnologyVersion": DefaultRedstoneRoutingTechnology.TechnologyVersion,
        "DustStairDecisionVersion": DustStairDecisionVersion,
        "TechnologyFingerprint": FixtureIdentity(Technology),
        "Fingerprint": FixtureIdentity({"Sources": Sources, "Technology": Technology}),
        "Sources": Sources,
    }


def ValidateFixtureBlockState(State: Mapping[str, Any]) -> None:
    """Reject numeric block properties outside the existing Minecraft domain."""
    Properties = State.get("Properties", {})
    if State["Name"] == "minecraft:repeater" and "delay" in Properties and Properties["delay"] not in {"1", "2", "3", "4"}:
        raise ValueError("invalid-repeater-delay: expected 1..4")
    if State["Name"] == "minecraft:redstone_wire" and "power" in Properties and Properties["power"] not in {str(Value) for Value in range(16)}:
        raise ValueError("invalid-dust-power: expected 0..15")


def CheckPhysicalFixture(
    Fixture: Mapping[str, Any],
    *,
    ExpectedFixtureSha256: str,
    InputVector: Mapping[str, bool],
) -> dict[str, Any]:
    """Query physical facts without receiving expected outcomes or a simulator.

    Legal covers supported static routing geometry only. An output request that
    needs control excitation, device simulation, or timing makes the aggregate
    Unknown. Unavailable facts carry a reason instead of an empty prediction.
    """
    Identity = FixtureIdentity(Fixture)
    if Identity != ExpectedFixtureSha256:
        raise ValueError("stale-fixture-identity")
    Inputs = Fixture.get("Inputs", ())
    Outputs = Fixture.get("Outputs", ())
    Names = {Port["Name"] for Port in Inputs}
    if set(InputVector) != Names or any(type(Value) is not bool for Value in InputVector.values()):
        raise ValueError("incomplete-or-nonboolean-input-vector")
    Blocks = {tuple(Item["Position"]): Item["State"] for Item in Fixture["Blocks"]}
    if len(Blocks) != len(Fixture["Blocks"]):
        raise ValueError("duplicate-block-position")
    for State in Blocks.values():
        ValidateFixtureBlockState(State)
    Technology = DefaultRedstoneRoutingTechnology
    Dust = {Position for Position, State in Blocks.items() if State["Name"] == "minecraft:redstone_wire"}
    Repeaters = {Position: State for Position, State in Blocks.items() if State["Name"] == "minecraft:repeater"}
    Electrical = {Position for Position, State in Blocks.items() if State["Name"] in ElectricalBlockNames}
    Solid = {Position for Position, State in Blocks.items() if State["Name"] not in NonSolidBlockNames}
    Air = {Position for Position, State in Blocks.items() if State["Name"] == "minecraft:air"}
    Occupied = set(Blocks) - Air
    RequiredSupport = {(X, Y - 1, Z) for X, Y, Z in Dust | set(Repeaters)}
    MissingSupport = RequiredSupport - Solid
    Graph = BuildPhysicalGraphs(
        {"Fixture": Dust},
        Occupied,
        Solid,
        Solid,
        BlockStates=Blocks,
    )["Fixture"]
    Edges = sorted((First, Second) for First, Neighbors in Graph.items() for Second in Neighbors)
    StairDecisions = []
    for First in sorted(Dust):
        for Second in Technology.NeighborPositions(First):
            if Second in Dust and First[1] < Second[1]:
                StairDecisions.append(QueryDustStair(
                    First,
                    Second,
                    ActualBlocks=Occupied,
                    ElectricalBlocks=Electrical,
                    SolidBlocks=Solid,
                    SupportPositions=Solid,
                    DustPositions=Dust,
                    BlockStates=Blocks,
                    SupportMode="Existing",
                ))
    Headroom = {Decision.HeadroomPosition for Decision in StairDecisions}
    BlockedHeadroom = {
        Decision.HeadroomPosition
        for Decision in StairDecisions
        if Decision.GeometryStatus is GeometryStatus.Blocked
    }
    ConflictCells, _, _, _ = AnalyzeFlatRouteConflicts({"Fixture": Dust})
    Reasons = []
    Unknown = []
    if MissingSupport:
        Reasons.append({"Code": "missing-support", "Positions": sorted(MissingSupport)})
    if ConflictCells:
        Reasons.append({"Code": "route-resource-conflict", "Positions": sorted(ConflictCells)})
    FacingFacts = []
    for Position, State in sorted(Repeaters.items()):
        Facing = State.get("Properties", {}).get("facing")
        try:
            Rear = RepeaterInputDelta(Facing)
            Front = RepeaterOutputDelta(Facing)
        except (ValueError, TypeError):
            Reasons.append({"Code": "invalid-repeater-facing", "Positions": [Position]})
            continue
        FacingFacts.append({
            "Position": list(Position), "InputFacing": Facing,
            "InputPosition": [Position[I] + Rear[I] for I in range(3)],
            "OutputPosition": [Position[I] + Front[I] for I in range(3)],
        })
    UnsupportedDevices = sorted(Position for Position, State in Blocks.items() if State["Name"] in {
        "minecraft:comparator", "minecraft:redstone_torch", "minecraft:redstone_wall_torch",
    })
    if UnsupportedDevices:
        Unknown.append({"Code": "device-behavior-unavailable", "Positions": UnsupportedDevices})
    for Decision in StairDecisions:
        if Decision.RouteClaimStatus is RouteClaimStatus.Unknown:
            Unknown.append({
                "Code": Decision.ReasonCode,
                "Positions": [Decision.HeadroomPosition],
                "DustStairInputIdentity": Decision.InputIdentity,
            })
    RouteInputs = Fixture.get("RouteInputs", {})
    RoutePositions = Dust | set(Repeaters)
    HasRouteContract = bool(RouteInputs) and set(RouteInputs) == Names and all(
        tuple(P) in Dust for P in RouteInputs.values()
    ) and all(tuple(P["Position"]) in RoutePositions for P in Outputs)
    RouteGraph = BuildPhysicalGraphs(
        {"Fixture": RoutePositions},
        Occupied,
        Solid,
        Solid,
        BlockStates=Blocks,
    )["Fixture"]
    # The existing repeater authority models flat directed route runs only.
    UnsupportedRepeaterGeometry = any(
        Neighbor[1] != P[1] or Neighbor in Repeaters
        for P in Repeaters for Neighbor in RouteGraph[P]
    )
    if UnsupportedRepeaterGeometry:
        Unknown.append({"Code": "nonflat-or-adjacent-repeater-transfer-unavailable", "Positions": sorted(Repeaters)})
    if (Inputs or Outputs) and not HasRouteContract:
        Unknown.append({"Code": "control-excitation-and-output-truth-unavailable", "Positions": sorted(Occupied)})
    Unavailable = {"Status": "Unknown", "Reason": "shared-routing-model-has-no-fixture-excitation-or-timing-contract"}
    ElectricalOutputs = Unavailable
    RootAssumptions = None
    if HasRouteContract and not Unknown and not Reasons:
        Powers = {}
        for Name, Root in sorted(RouteInputs.items()):
            if InputVector[Name]:
                for P, Power in PropagateRoutePower(tuple(Root), RouteGraph, {
                    P: State["Properties"]["facing"] for P, State in Repeaters.items()
                }).items():
                    Powers[P] = max(Powers.get(P, 0), Power)
        ElectricalOutputs = {"Status": "Available", "Values": {
            Port["Name"]: Powers.get(tuple(Port["Position"]), 0) > 0 for Port in Outputs
        }, "Scope": "conditional-powered-route-transfer"}
        RootAssumptions = {Name: 15 if InputVector[Name] else 0 for Name in sorted(RouteInputs)}
    elif not Inputs and not Outputs and not Unknown and not Reasons:
        ElectricalOutputs = {"Status": "Available", "Values": {}, "Scope": "no-electrical-queries"}
    return {
        "Status": "Illegal" if Reasons else "Unknown" if Unknown else "Legal",
        "FixtureSha256": Identity,
        "Model": ModelIdentity(),
        "CheckedPositions": sorted(Occupied | RequiredSupport | Headroom),
        "Assumptions": [
            "Unlisted positions are air in a closed fixture.",
            "Solid classification uses the shared routing model NonSolidBlockNames complement.",
            "Dust edges describe the shared static graph, not simulator blockstate wire arms.",
            "The shared dust-stair query owns headroom classification and geometric connection; blocked geometry alone does not make the fixture illegal.",
            "No net ownership labels are supplied; only within-fixture resource conflicts are checked.",
            "Conditional route transfer assumes excitation enters only declared dust roots at 0 or 15; native root readback must independently verify the final stable interval.",
        ],
        "Reasons": Reasons + Unknown,
        "Physical": {
            "DustConnections": [[list(A), list(B)] for A, B in Edges],
            "SupportPositions": [list(P) for P in sorted(RequiredSupport)],
            "MissingSupport": [list(P) for P in sorted(MissingSupport)],
            "HeadroomPositions": [list(P) for P in sorted(Headroom)],
            "BlockedHeadroom": [list(P) for P in sorted(BlockedHeadroom)],
            "DustStairDecisions": [
                Decision.ToDictionary() for Decision in StairDecisions
            ],
            "RepeaterDirections": FacingFacts,
            "ConflictPositions": [list(P) for P in sorted(ConflictCells)],
            "CrossNetConflicts": {"Status": "Unknown", "Reason": "fixture-has-no-net-ownership-contract"},
        },
        "Electrical": {"InputVector": dict(sorted(InputVector.items())), "Outputs": ElectricalOutputs, "SettlementTicks": Unavailable, "RequiredRootPower": RootAssumptions},
    }
