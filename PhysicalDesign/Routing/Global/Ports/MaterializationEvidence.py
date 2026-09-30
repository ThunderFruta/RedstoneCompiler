"""Bounded snapshots of an already observed materialization rejection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping

from ....Contracts.Core import Position3
from ....Resources.ResourceGraph import (
    RoutingResourceClaims,
    RoutingResourceId,
    RoutingResourceKind,
)


MaximumInspectedConflicts = 256
MaximumRetainedConflicts = 16
MaximumCapturedClaimCells = 256
MaximumCapturedNodes = 64


@dataclass(frozen=True)
class MaterializationConflictWitness:
    """Claim memberships at one resource reported by the physical predicate."""

    Kind: str
    Position: Position3
    ClaimKinds: tuple[str, ...]

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Kind": self.Kind,
            "Position": list(self.Position),
            "ClaimKinds": list(self.ClaimKinds),
        }


@dataclass(frozen=True)
class MaterializationSelfConflictEvidence:
    """Predicate facts only; enclosing diagnostics own caller/run identity.

    Complete capture describes the bounded predicate inputs, not a complete
    physical scene, contributor provenance, or reusable routing result.
    """

    Signal: str
    ObservedConflictCount: int
    Conflicts: tuple[MaterializationConflictWitness, ...]
    ClaimCounts: tuple[tuple[str, int], ...]
    Claims: tuple[tuple[str, tuple[Position3, ...]], ...] | None
    NodeCount: int
    Nodes: tuple[Position3, ...] | None
    Omissions: tuple[str, ...]
    SchemaVersion: ClassVar[str] = "materialization-self-conflict-evidence-v1"

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": self.SchemaVersion,
            "Scope": "FirstDecisiveMaterializationSelfClaimPredicate",
            "Predicate": "FindSelfClaimConflicts",
            "Signal": self.Signal,
            "CaptureStatus": "Partial" if self.Omissions else "Complete",
            "ObservedConflictCount": self.ObservedConflictCount,
            "RetainedConflictCount": len(self.Conflicts),
            "ConflictCoverage": (
                "Complete" if len(self.Conflicts) == self.ObservedConflictCount
                else "Partial" if self.Conflicts else "Unavailable"
            ),
            "Conflicts": [Value.ToDictionary() for Value in self.Conflicts],
            "ClaimCounts": dict(self.ClaimCounts),
            "Claims": (
                {Kind: [list(Position) for Position in Positions]
                 for Kind, Positions in self.Claims}
                if self.Claims is not None else None
            ),
            "NodeCount": self.NodeCount,
            "Nodes": (
                [list(Position) for Position in self.Nodes]
                if self.Nodes is not None else None
            ),
            "ContributorProvenance": "Unavailable",
            "EvaluationIdentity": "NotCaptured",
            "Omissions": list(self.Omissions),
            "Limits": {
                "InspectedConflicts": MaximumInspectedConflicts,
                "RetainedConflicts": MaximumRetainedConflicts,
                "ClaimCells": MaximumCapturedClaimCells,
                "Nodes": MaximumCapturedNodes,
            },
        }


def CaptureMaterializationSelfConflict(
    Signal: str,
    Nodes: set[Position3],
    Claims: RoutingResourceClaims,
    ObservedConflicts: Mapping[RoutingResourceId, tuple[str, ...]],
) -> MaterializationSelfConflictEvidence:
    """Copy already computed facts without a second physical evaluation.

    Oversized inputs are counted but not traversed. The bounded conflict
    projection consults membership in the same claim sets used by the owner
    predicate; it does not build geometry, find owners, or run legality again.
    """
    if type(Signal) is not str or not Signal:
        raise ValueError("self-conflict evidence requires a signal")
    if type(Claims) is not RoutingResourceClaims:
        raise TypeError("self-conflict evidence requires routing resource claims")
    ConflictCount = len(ObservedConflicts)
    if not ConflictCount:
        raise ValueError("self-conflict evidence requires an observed rejection")
    ClaimSets = (
        ("Wire", Claims.WireCells),
        ("Support", Claims.SupportCells),
        ("Air", Claims.RequiredAirCells),
        ("Electrical", Claims.ElectricalCells),
    )
    ClaimCounts = tuple((Kind, len(Cells)) for Kind, Cells in ClaimSets)
    Omissions: list[str] = []
    CapturedClaims = None
    if sum(Count for _Kind, Count in ClaimCounts) <= MaximumCapturedClaimCells:
        CapturedClaims = tuple(
            (Kind, tuple(sorted(Cells))) for Kind, Cells in ClaimSets
        )
    else:
        Omissions.append("ClaimCellLimit")
    NodeCount = len(Nodes)
    CapturedNodes = None
    if NodeCount <= MaximumCapturedNodes:
        CapturedNodes = tuple(sorted(Nodes))
    else:
        Omissions.append("NodeLimit")
    Witnesses = []
    if ConflictCount > MaximumInspectedConflicts:
        Omissions.append("ConflictInspectionLimit")
    else:
        Resources = sorted(
            ObservedConflicts, key=lambda Value: (Value.Kind.value, Value.Position),
        )
        if ConflictCount > MaximumRetainedConflicts:
            Omissions.append("ConflictRetentionLimit")
        for Resource in Resources[:MaximumRetainedConflicts]:
            if ObservedConflicts[Resource] != (Signal,):
                raise ValueError("self-conflict owner differs from the evaluated signal")
            Position = Resource.Position
            if Resource.Kind is RoutingResourceKind.Support:
                if Position not in Claims.SupportCells:
                    raise ValueError("observed support conflict lacks its claim")
                Kinds = ("Support",) + tuple(
                    Kind for Kind, Cells in (
                        ("Wire", Claims.WireCells), ("Air", Claims.RequiredAirCells),
                    ) if Position in Cells
                )
            elif Resource.Kind is RoutingResourceKind.Air:
                if Position not in Claims.RequiredAirCells:
                    raise ValueError("observed air conflict lacks its claim")
                Kinds = ("Air",) + (("Wire",) if Position in Claims.WireCells else ())
            else:
                raise ValueError("resource is not a materialization self-conflict")
            if len(Kinds) < 2:
                raise ValueError("observed self-conflict lacks the other claim")
            Witnesses.append(MaterializationConflictWitness(
                Resource.Kind.value, Position, Kinds,
            ))
    return MaterializationSelfConflictEvidence(
        Signal, ConflictCount, tuple(Witnesses), ClaimCounts,
        CapturedClaims, NodeCount, CapturedNodes, tuple(sorted(Omissions)),
    )
