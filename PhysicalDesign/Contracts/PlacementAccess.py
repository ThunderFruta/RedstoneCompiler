"""Immutable contracts for routing-aware placement and pin access."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .Core import Position3
from ..Resources.ResourceGraph import (
    RoutingReservation,
    RoutingResourceClaims,
    RoutingResourceKind,
)
from ..Runtime.Reliability import BuildStableFingerprint


def _PositionDictionary(Position: Position3) -> list[int]:
    return list(Position)


def _RelativePosition(
    Position: Position3,
    Origin: Position3,
) -> Position3:
    return tuple(
        Position[Index] - Origin[Index]
        for Index in range(3)
    )


def _ClaimsDictionary(Claims: RoutingResourceClaims) -> dict[str, object]:
    return {
        "WireCells": [list(Value) for Value in sorted(Claims.WireCells)],
        "SupportCells": [
            list(Value) for Value in sorted(Claims.SupportCells)
        ],
        "RequiredAirCells": [
            list(Value) for Value in sorted(Claims.RequiredAirCells)
        ],
        "ElectricalCells": [
            list(Value) for Value in sorted(Claims.ElectricalCells)
        ],
    }


def _RelativeClaimsDictionary(
    Claims: RoutingResourceClaims,
    Origin: Position3,
) -> dict[str, object]:
    return {
        "WireCells": [
            list(_RelativePosition(Value, Origin))
            for Value in sorted(Claims.WireCells)
        ],
        "SupportCells": [
            list(_RelativePosition(Value, Origin))
            for Value in sorted(Claims.SupportCells)
        ],
        "RequiredAirCells": [
            list(_RelativePosition(Value, Origin))
            for Value in sorted(Claims.RequiredAirCells)
        ],
        "ElectricalCells": [
            list(_RelativePosition(Value, Origin))
            for Value in sorted(Claims.ElectricalCells)
        ],
    }


def _ReservationDictionary(
    Reservation: RoutingReservation,
) -> dict[str, object]:
    return {
        "Signal": Reservation.Signal,
        "Resource": str(Reservation.Resource),
        "Position": list(Reservation.Position),
        "Purpose": Reservation.Purpose,
        "InputFacing": Reservation.InputFacing,
    }


def _MergeClaims(
    Values: tuple[RoutingResourceClaims, ...],
) -> RoutingResourceClaims:
    return RoutingResourceClaims(
        WireCells=frozenset().union(*(Value.WireCells for Value in Values)),
        SupportCells=frozenset().union(
            *(Value.SupportCells for Value in Values)
        ),
        RequiredAirCells=frozenset().union(
            *(Value.RequiredAirCells for Value in Values)
        ),
        ElectricalCells=frozenset().union(
            *(Value.ElectricalCells for Value in Values)
        ),
    )


def _ValidateHorizontalStep(First: Position3, Second: Position3) -> None:
    Delta = tuple(
        Second[Index] - First[Index]
        for Index in range(3)
    )
    if Delta[1] != 0 or sum(abs(Value) for Value in Delta) != 1:
        raise ValueError("pin-access geometry contains a non-cardinal step")


@dataclass(frozen=True)
class PhysicalPinAccessTemplate:
    """One technology-proven cell-local pin-access alternative."""

    CatalogVersion: str
    CellKind: str
    TemplateId: str
    PatternFamily: str
    PinId: str
    ConnectionPosition: Position3
    ApproachDirection: Position3
    TangentialSign: int
    FirstLegNodes: tuple[Position3, ...]
    FirstTrackNode: Position3
    BlockRoles: tuple[tuple[Position3, str], ...]
    RepeaterPathIndex: int
    AllowedRoutingLayers: tuple[int, ...]
    TechnologyFingerprint: str

    @classmethod
    def FromDictionary(cls, Value: dict[str, object]) -> PhysicalPinAccessTemplate:
        from .PlacementAccessCodec import ReadContract
        return ReadContract(cls, Value)

    def __post_init__(self) -> None:
        if not all((
            self.CatalogVersion,
            self.CellKind,
            self.TemplateId,
            self.PatternFamily,
            self.PinId,
            self.TechnologyFingerprint,
        )):
            raise ValueError("pin-access template requires stable identities")
        if self.PatternFamily not in {"straight", "planar-jog"}:
            raise ValueError("pin-access template family is unsupported")
        ExpectedSigns = (
            {0} if self.PatternFamily == "straight" else {-1, 1}
        )
        if self.TangentialSign not in ExpectedSigns:
            raise ValueError("pin-access template tangential sign is invalid")
        if (
            self.ApproachDirection[1] != 0
            or sum(abs(Value) for Value in self.ApproachDirection) != 1
        ):
            raise ValueError("pin-access template face must be horizontal")
        if not self.FirstLegNodes:
            raise ValueError("pin-access template requires a first leg")
        if self.FirstLegNodes[0] != self.ConnectionPosition:
            raise ValueError("pin-access first leg must begin at its connection")
        for First, Second in zip(
            self.FirstLegNodes,
            self.FirstLegNodes[1:],
        ):
            _ValidateHorizontalStep(First, Second)
        _ValidateHorizontalStep(self.FirstLegNodes[-1], self.FirstTrackNode)
        if len(set(self.FirstLegNodes)) != len(self.FirstLegNodes):
            raise ValueError("pin-access first leg repeats a node")
        if self.FirstTrackNode in self.FirstLegNodes:
            raise ValueError("pin-access first-track node repeats the first leg")
        if not 0 <= self.RepeaterPathIndex < len(self.FirstLegNodes):
            raise ValueError("pin-access repeater index is outside the first leg")
        if tuple(Position for Position, _Role in self.BlockRoles) != (
            self.FirstLegNodes
        ):
            raise ValueError("pin-access block roles must follow the first leg")
        if tuple(Role for _Position, Role in self.BlockRoles).count(
            "repeater"
        ) != 1:
            raise ValueError("pin-access template requires exactly one repeater")
        if self.BlockRoles[self.RepeaterPathIndex][1] != "repeater":
            raise ValueError("pin-access repeater index and block role disagree")
        if any(
            Role not in {"dust", "repeater"}
            for _Position, Role in self.BlockRoles
        ):
            raise ValueError("pin-access template has an unknown block role")
        if (
            not self.AllowedRoutingLayers
            or self.AllowedRoutingLayers
            != tuple(sorted(set(self.AllowedRoutingLayers)))
            or any(Value < 0 for Value in self.AllowedRoutingLayers)
        ):
            raise ValueError("pin-access routing layers must be sorted and unique")

    @property
    def BridgePosition(self) -> Position3:
        return tuple(
            self.ConnectionPosition[Index] - self.ApproachDirection[Index]
            for Index in range(3)
        )

    @property
    def ProofFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": "physical-pin-access-template-proof-v1",
            "PatternFamily": self.PatternFamily,
            "ConnectionPosition": self.ConnectionPosition,
            "ApproachDirection": self.ApproachDirection,
            "TangentialSign": self.TangentialSign,
            "FirstLegNodes": self.FirstLegNodes,
            "FirstTrackNode": self.FirstTrackNode,
            "BlockRoles": self.BlockRoles,
            "RepeaterPathIndex": self.RepeaterPathIndex,
            "AllowedRoutingLayers": self.AllowedRoutingLayers,
            "TechnologyFingerprint": self.TechnologyFingerprint,
        })

    @property
    def TemplateFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": "physical-pin-access-template-v1",
            "CatalogVersion": self.CatalogVersion,
            "CellKind": self.CellKind,
            "TemplateId": self.TemplateId,
            "PinId": self.PinId,
            "ProofFingerprint": self.ProofFingerprint,
        })

    def StructuralIdentity(self) -> tuple[object, ...]:
        return (
            self.PatternFamily != "straight",
            self.CellKind,
            self.PatternFamily,
            self.TangentialSign,
            self.TemplateId,
            self.PinId,
            self.ProofFingerprint,
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "CatalogVersion": self.CatalogVersion,
            "CellKind": self.CellKind,
            "TemplateId": self.TemplateId,
            "TemplateFingerprint": self.TemplateFingerprint,
            "ProofFingerprint": self.ProofFingerprint,
            "PatternFamily": self.PatternFamily,
            "PinId": self.PinId,
            "ConnectionPosition": list(self.ConnectionPosition),
            "BridgePosition": list(self.BridgePosition),
            "ApproachDirection": list(self.ApproachDirection),
            "TangentialSign": self.TangentialSign,
            "FirstLegNodes": [
                list(Value) for Value in self.FirstLegNodes
            ],
            "FirstTrackNode": list(self.FirstTrackNode),
            "BlockRoles": [
                {"Position": list(Position), "Role": Role}
                for Position, Role in self.BlockRoles
            ],
            "RepeaterPathIndex": self.RepeaterPathIndex,
            "AllowedRoutingLayers": list(self.AllowedRoutingLayers),
            "TechnologyFingerprint": self.TechnologyFingerprint,
        }


@dataclass(frozen=True)
class PlacedPinAccessOption:
    """One exact pin-access template after a placed-cell transform."""

    Signal: str
    GateName: str
    GateKind: str
    Role: str
    PinId: str
    CatalogVersion: str
    TemplateId: str
    PatternFamily: str
    TemplateFingerprint: str
    TemplateProofFingerprint: str
    TechnologyFingerprint: str
    ResourceModelFingerprint: str
    Terminal: Position3
    Face: Position3
    Layer: int
    FirstLegNodes: tuple[Position3, ...]
    FirstTrackNode: Position3
    BlockRoles: tuple[tuple[Position3, str], ...]
    Claims: RoutingResourceClaims
    RepeaterReservations: tuple[RoutingReservation, ...]
    Template: PhysicalPinAccessTemplate | None = None

    @classmethod
    def FromDictionary(cls, Value: dict[str, object]) -> PlacedPinAccessOption:
        from .PlacementAccessCodec import ReadContract
        Result = ReadContract(cls, Value)
        if Result.Template is None:
            raise ValueError("serialized access option requires its template proof")
        return Result

    def __post_init__(self) -> None:
        if self.Role not in {"Source", "Target"}:
            raise ValueError("placed pin-access role is invalid")
        if self.Template is not None and (
            self.Template.TemplateFingerprint != self.TemplateFingerprint
            or self.Template.ProofFingerprint != self.TemplateProofFingerprint
            or self.Template.CatalogVersion != self.CatalogVersion
            or self.Template.TechnologyFingerprint != self.TechnologyFingerprint
            or self.Template.CellKind != self.GateKind
            or self.Template.PinId != self.PinId
            or self.Template.TemplateId != self.TemplateId
            or self.Template.PatternFamily != self.PatternFamily
        ):
            raise ValueError("placed pin-access template proof identity mismatch")
        if not all((
            self.Signal,
            self.GateName,
            self.GateKind,
            self.PinId,
            self.CatalogVersion,
            self.TemplateId,
            self.PatternFamily,
            self.TemplateFingerprint,
            self.TemplateProofFingerprint,
            self.TechnologyFingerprint,
            self.ResourceModelFingerprint,
        )):
            raise ValueError("placed pin-access option requires identities")
        if self.Layer < 0:
            raise ValueError("placed pin-access layer cannot be negative")
        if not self.FirstLegNodes or self.FirstLegNodes[0] != self.Terminal:
            raise ValueError("placed pin-access first leg must start at terminal")
        for First, Second in zip(
            self.FirstLegNodes,
            self.FirstLegNodes[1:],
        ):
            _ValidateHorizontalStep(First, Second)
        _ValidateHorizontalStep(self.FirstLegNodes[-1], self.FirstTrackNode)
        if tuple(Position for Position, _Role in self.BlockRoles) != (
            self.FirstLegNodes
        ):
            raise ValueError("placed pin-access block roles are misaligned")
        if self.Claims.WireCells != frozenset(self.FirstLegNodes):
            raise ValueError("placed pin-access claims do not own its first leg")
        if len(self.RepeaterReservations) != 1:
            raise ValueError("placed pin-access option requires one repeater")
        RepeaterPositions = tuple(
            Position
            for Position, BlockRole in self.BlockRoles
            if BlockRole == "repeater"
        )
        Reservation = self.RepeaterReservations[0]
        if (
            RepeaterPositions != (Reservation.Position,)
            or Reservation.Signal != self.Signal
            or Reservation.Resource.Position != Reservation.Position
            or Reservation.Resource.Kind is not RoutingResourceKind.Wire
            or Reservation.Purpose != "PinAccessRepeater"
            or Reservation.InputFacing is None
        ):
            raise ValueError("placed pin-access repeater reservation is invalid")

    @property
    def Path(self) -> tuple[Position3, ...]:
        """Compatibility alias for the existing fixed-placement solver."""
        return self.FirstLegNodes

    @property
    def ApproachDirection(self) -> Position3:
        """Compatibility alias for legacy straight-access consumers."""
        return self.Face

    @property
    def PatternId(self) -> str:
        return self.TemplateId

    @property
    def CatalogAccessLength(self) -> int:
        return len(self.FirstLegNodes)

    @property
    def CatalogMatched(self) -> bool:
        return True

    @property
    def BendCount(self) -> int:
        Nodes = (*self.FirstLegNodes, self.FirstTrackNode)
        Directions = tuple(
            tuple(
                Second[Index] - First[Index]
                for Index in range(3)
            )
            for First, Second in zip(Nodes, Nodes[1:])
        )
        return sum(
            First != Second
            for First, Second in zip(Directions, Directions[1:])
        )

    @property
    def VerticalTransitionCount(self) -> int:
        Nodes = (*self.FirstLegNodes, self.FirstTrackNode)
        return sum(
            First[1] != Second[1]
            for First, Second in zip(Nodes, Nodes[1:])
        )

    @property
    def AnonymousGeometryFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": "placed-pin-access-anonymous-geometry-v1",
            "GateKind": self.GateKind,
            "Role": self.Role,
            "PinId": self.PinId,
            "CatalogVersion": self.CatalogVersion,
            "TemplateFingerprint": self.TemplateFingerprint,
            "Face": self.Face,
            "Layer": self.Layer,
            "FirstLegNodes": [
                _RelativePosition(Value, self.Terminal)
                for Value in self.FirstLegNodes
            ],
            "FirstTrackNode": _RelativePosition(
                self.FirstTrackNode,
                self.Terminal,
            ),
            "BlockRoles": [
                (
                    _RelativePosition(Position, self.Terminal),
                    Role,
                )
                for Position, Role in self.BlockRoles
            ],
            "Claims": _RelativeClaimsDictionary(
                self.Claims,
                self.Terminal,
            ),
            "Repeaters": [
                {
                    "Position": _RelativePosition(
                        Value.Position,
                        self.Terminal,
                    ),
                    "InputFacing": Value.InputFacing,
                    "Purpose": Value.Purpose,
                }
                for Value in self.RepeaterReservations
            ],
            "TechnologyFingerprint": self.TechnologyFingerprint,
        })

    @property
    def PlacedBindingFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": "placed-pin-access-binding-v1",
            "AnonymousGeometryFingerprint": self.AnonymousGeometryFingerprint,
            "Signal": self.Signal,
            "GateName": self.GateName,
            "Terminal": self.Terminal,
            "ResourceModelFingerprint": self.ResourceModelFingerprint,
        })

    @property
    def SelectionFingerprint(self) -> str:
        """Compatibility alias for the existing exact access solver."""
        return self.PlacedBindingFingerprint

    def TerminalIdentity(self) -> tuple[str, str, str, str]:
        return self.Signal, self.GateName, self.Role, self.PinId

    def RankKey(self) -> tuple[object, ...]:
        return (
            self.BendCount,
            self.VerticalTransitionCount,
            len(self.FirstLegNodes),
            self.PlacedBindingFingerprint,
        )

    def StructuralIdentity(self) -> tuple[object, ...]:
        return (
            self.TerminalIdentity(),
            self.CatalogVersion,
            self.TemplateId,
            self.TemplateFingerprint,
            self.Terminal,
            self.Face,
            self.Layer,
            self.FirstLegNodes,
            self.FirstTrackNode,
            self.BlockRoles,
            tuple(sorted(map(str, self.Claims.ResourceIds))),
            tuple(
                (
                    Value.Position,
                    Value.Purpose,
                    Value.InputFacing,
                )
                for Value in self.RepeaterReservations
            ),
            self.TechnologyFingerprint,
            self.ResourceModelFingerprint,
        )

    def ToIdentityDictionary(self) -> dict[str, object]:
        return {
            "Signal": self.Signal,
            "GateName": self.GateName,
            "GateKind": self.GateKind,
            "Role": self.Role,
            "PinId": self.PinId,
            "CatalogVersion": self.CatalogVersion,
            "TemplateId": self.TemplateId,
            "PatternId": self.PatternId,
            "PatternFamily": self.PatternFamily,
            "TemplateFingerprint": self.TemplateFingerprint,
            "TemplateProofFingerprint": self.TemplateProofFingerprint,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "ResourceModelFingerprint": self.ResourceModelFingerprint,
            "Terminal": list(self.Terminal),
            "Face": list(self.Face),
            "ApproachDirection": list(self.ApproachDirection),
            "Layer": self.Layer,
            "FirstLegNodes": [list(Value) for Value in self.FirstLegNodes],
            "Path": [list(Value) for Value in self.Path],
            "FirstTrackNode": list(self.FirstTrackNode),
            "BlockRoles": [
                {"Position": list(Position), "Role": Role}
                for Position, Role in self.BlockRoles
            ],
            "Claims": _ClaimsDictionary(self.Claims),
            "RepeaterReservations": [
                _ReservationDictionary(Value)
                for Value in self.RepeaterReservations
            ],
            "BendCount": self.BendCount,
            "VerticalTransitionCount": self.VerticalTransitionCount,
            "AnonymousGeometryFingerprint": (
                self.AnonymousGeometryFingerprint
            ),
            "PlacedBindingFingerprint": self.PlacedBindingFingerprint,
            "SelectionFingerprint": self.SelectionFingerprint,
            "CatalogAccessLength": self.CatalogAccessLength,
            "CatalogMatched": self.CatalogMatched,
        }

    def ToDictionary(self) -> dict[str, object]:
        return {
            **self.ToIdentityDictionary(),
            "Template": self.Template.ToDictionary() if self.Template else None,
        }


class PlacementAccessPatternAttemptStatus(str, Enum):
    """Typed disposition of one required catalog-pattern evaluation."""

    Legal = "Legal"
    Rejected = "Rejected"
    Deduplicated = "Deduplicated"
    NotEvaluated = "NotEvaluated"
    Unknown = "Unknown"


class PlacementAccessPatternAttemptReason(str, Enum):
    """Closed reasons retained for one catalog-pattern disposition."""

    Legal = "Legal"
    DuplicateOption = "DuplicateOption"
    TerminalOrBridgeUnavailable = "TerminalOrBridgeUnavailable"
    FirstLegOccupied = "FirstLegOccupied"
    PrimitiveUnavailable = "PrimitiveUnavailable"
    ClaimOccupancyConflict = "ClaimOccupancyConflict"
    SelfClaimConflict = "SelfClaimConflict"
    ForeignClaimConflict = "ForeignClaimConflict"
    StaticKeepOut = "StaticKeepOut"
    ForeignStaticExclusion = "ForeignStaticExclusion"
    WorkCap = "WorkCap"
    Deadline = "Deadline"
    InputDrift = "InputDrift"
    UnknownPhysicalDecision = "UnknownPhysicalDecision"


def BuildPlacementAccessPatternAttemptId(
    *,
    DomainId: str,
    TemplateId: str,
    PatternFamily: str,
    TemplateFingerprint: str,
    Layer: int,
    CatalogVersion: str,
    TechnologyFingerprint: str,
    ResourceModelFingerprint: str,
) -> str:
    """Identify a required semantic pattern evaluation without list position."""
    return BuildStableFingerprint({
        "Kind": "placed-pin-access-pattern-attempt-v1",
        "DomainId": DomainId,
        "TemplateId": TemplateId,
        "PatternFamily": PatternFamily,
        "TemplateFingerprint": TemplateFingerprint,
        "Layer": Layer,
        "CatalogVersion": CatalogVersion,
        "TechnologyFingerprint": TechnologyFingerprint,
        "ResourceModelFingerprint": ResourceModelFingerprint,
    })


def BuildPlacementAccessDomainControlsFingerprint(
    *,
    EnabledPatternFamilies: tuple[str, ...],
    CatalogVersion: str,
    MaximumGenerationWork: int,
) -> str:
    """Bind the portable domain to its exact producer controls."""
    if (
        not EnabledPatternFamilies
        or EnabledPatternFamilies
        != tuple(sorted(set(EnabledPatternFamilies)))
        or any(
            type(Value) is not str
            or Value not in {"straight", "planar-jog"}
            for Value in EnabledPatternFamilies
        )
    ):
        raise ValueError("placed pin-access enabled families are invalid")
    if type(CatalogVersion) is not str or not CatalogVersion:
        raise ValueError("placed pin-access catalog version is invalid")
    if type(MaximumGenerationWork) is not int or MaximumGenerationWork < 1:
        raise ValueError("placed pin-access generation cap must be positive")
    return BuildStableFingerprint({
        "Kind": "placed-pin-access-domain-controls-v1",
        "EnabledPatternFamilies": EnabledPatternFamilies,
        "CatalogVersion": CatalogVersion,
        "MaximumGenerationWork": MaximumGenerationWork,
    })


@dataclass(frozen=True)
class PlacementAccessEvaluationControls:
    """Exact current policy controls required for access authority."""

    EnabledPatternFamilies: tuple[str, ...]
    CatalogVersion: str
    MaximumGenerationWork: int
    MaximumAssignmentExpansions: int
    SchemaVersion: str = "placement-access-evaluation-controls-v1"

    @classmethod
    def FromDictionary(
        cls,
        Value: dict[str, object],
    ) -> PlacementAccessEvaluationControls:
        from .PlacementAccessCodec import ReadContract
        return ReadContract(cls, Value)

    def __post_init__(self) -> None:
        if self.SchemaVersion != "placement-access-evaluation-controls-v1":
            raise ValueError("unsupported placement-access evaluation controls")
        BuildPlacementAccessDomainControlsFingerprint(
            EnabledPatternFamilies=self.EnabledPatternFamilies,
            CatalogVersion=self.CatalogVersion,
            MaximumGenerationWork=self.MaximumGenerationWork,
        )
        if (
            type(self.MaximumAssignmentExpansions) is not int
            or self.MaximumAssignmentExpansions < 1
        ):
            raise ValueError(
                "placement-access assignment cap must be a positive exact integer"
            )

    @property
    def ControlsFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": self.SchemaVersion,
            "EnabledPatternFamilies": self.EnabledPatternFamilies,
            "CatalogVersion": self.CatalogVersion,
            "MaximumGenerationWork": self.MaximumGenerationWork,
            "MaximumAssignmentExpansions": self.MaximumAssignmentExpansions,
        })

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": self.SchemaVersion,
            "ControlsFingerprint": self.ControlsFingerprint,
            "EnabledPatternFamilies": list(self.EnabledPatternFamilies),
            "CatalogVersion": self.CatalogVersion,
            "MaximumGenerationWork": self.MaximumGenerationWork,
            "MaximumAssignmentExpansions": self.MaximumAssignmentExpansions,
        }


@dataclass(frozen=True)
class PlacedPinAccessPatternRequirement:
    """One exact template/layer member of a terminal's required universe."""

    AttemptId: str
    DomainId: str
    TemplateId: str
    PatternFamily: str
    TemplateFingerprint: str
    Layer: int
    CatalogVersion: str
    TechnologyFingerprint: str
    ResourceModelFingerprint: str

    @classmethod
    def FromDictionary(
        cls,
        Value: dict[str, object],
    ) -> PlacedPinAccessPatternRequirement:
        from .PlacementAccessCodec import ReadContract
        return ReadContract(cls, Value)

    def __post_init__(self) -> None:
        if not all((
            self.AttemptId,
            self.DomainId,
            self.TemplateId,
            self.PatternFamily,
            self.TemplateFingerprint,
            self.CatalogVersion,
            self.TechnologyFingerprint,
            self.ResourceModelFingerprint,
        )):
            raise ValueError("pin-access pattern requirement requires identities")
        if self.PatternFamily not in {"straight", "planar-jog"}:
            raise ValueError("pin-access pattern requirement family is unsupported")
        if type(self.Layer) is not int or self.Layer < 0:
            raise ValueError("pin-access pattern requirement layer is invalid")
        ExpectedId = BuildPlacementAccessPatternAttemptId(
            DomainId=self.DomainId,
            TemplateId=self.TemplateId,
            PatternFamily=self.PatternFamily,
            TemplateFingerprint=self.TemplateFingerprint,
            Layer=self.Layer,
            CatalogVersion=self.CatalogVersion,
            TechnologyFingerprint=self.TechnologyFingerprint,
            ResourceModelFingerprint=self.ResourceModelFingerprint,
        )
        if self.AttemptId != ExpectedId:
            raise ValueError("pin-access pattern requirement identity mismatch")

    def RankKey(self) -> tuple[object, ...]:
        return self.TemplateId, self.Layer, self.AttemptId

    def SemanticIdentity(self) -> tuple[object, ...]:
        return (
            self.AttemptId,
            self.DomainId,
            self.TemplateId,
            self.PatternFamily,
            self.TemplateFingerprint,
            self.Layer,
            self.CatalogVersion,
            self.TechnologyFingerprint,
            self.ResourceModelFingerprint,
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "AttemptId": self.AttemptId,
            "DomainId": self.DomainId,
            "TemplateId": self.TemplateId,
            "PatternFamily": self.PatternFamily,
            "TemplateFingerprint": self.TemplateFingerprint,
            "Layer": self.Layer,
            "CatalogVersion": self.CatalogVersion,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "ResourceModelFingerprint": self.ResourceModelFingerprint,
        }


@dataclass(frozen=True)
class PlacedPinAccessPatternAttempt:
    """One identity-bound required pattern and its exact evaluation result."""

    AttemptId: str
    DomainId: str
    TemplateId: str
    PatternFamily: str
    TemplateFingerprint: str
    Layer: int
    CatalogVersion: str
    TechnologyFingerprint: str
    ResourceModelFingerprint: str
    Status: PlacementAccessPatternAttemptStatus
    Reason: PlacementAccessPatternAttemptReason
    OptionFingerprint: str | None

    @classmethod
    def FromDictionary(cls, Value: dict[str, object]) -> PlacedPinAccessPatternAttempt:
        from .PlacementAccessCodec import ReadContract
        return ReadContract(cls, Value)

    def __post_init__(self) -> None:
        if not all((
            self.AttemptId,
            self.DomainId,
            self.TemplateId,
            self.PatternFamily,
            self.TemplateFingerprint,
            self.CatalogVersion,
            self.TechnologyFingerprint,
            self.ResourceModelFingerprint,
        )):
            raise ValueError("pin-access pattern attempt requires identities")
        if self.PatternFamily not in {"straight", "planar-jog"}:
            raise ValueError("pin-access pattern attempt family is unsupported")
        if type(self.Layer) is not int or self.Layer < 0:
            raise ValueError("pin-access pattern attempt layer is invalid")
        if type(self.Status) is not PlacementAccessPatternAttemptStatus:
            raise ValueError("pin-access pattern attempt status must be typed")
        if type(self.Reason) is not PlacementAccessPatternAttemptReason:
            raise ValueError("pin-access pattern attempt reason must be typed")
        ExpectedId = BuildPlacementAccessPatternAttemptId(
            DomainId=self.DomainId,
            TemplateId=self.TemplateId,
            PatternFamily=self.PatternFamily,
            TemplateFingerprint=self.TemplateFingerprint,
            Layer=self.Layer,
            CatalogVersion=self.CatalogVersion,
            TechnologyFingerprint=self.TechnologyFingerprint,
            ResourceModelFingerprint=self.ResourceModelFingerprint,
        )
        if self.AttemptId != ExpectedId:
            raise ValueError("pin-access pattern attempt identity mismatch")
        ExpectedReasons = {
            PlacementAccessPatternAttemptStatus.Legal: {
                PlacementAccessPatternAttemptReason.Legal,
            },
            PlacementAccessPatternAttemptStatus.Deduplicated: {
                PlacementAccessPatternAttemptReason.DuplicateOption,
            },
            PlacementAccessPatternAttemptStatus.NotEvaluated: {
                PlacementAccessPatternAttemptReason.WorkCap,
                PlacementAccessPatternAttemptReason.Deadline,
                PlacementAccessPatternAttemptReason.InputDrift,
            },
            PlacementAccessPatternAttemptStatus.Unknown: {
                PlacementAccessPatternAttemptReason.UnknownPhysicalDecision,
            },
            PlacementAccessPatternAttemptStatus.Rejected: {
                PlacementAccessPatternAttemptReason.TerminalOrBridgeUnavailable,
                PlacementAccessPatternAttemptReason.FirstLegOccupied,
                PlacementAccessPatternAttemptReason.PrimitiveUnavailable,
                PlacementAccessPatternAttemptReason.ClaimOccupancyConflict,
                PlacementAccessPatternAttemptReason.SelfClaimConflict,
                PlacementAccessPatternAttemptReason.ForeignClaimConflict,
                PlacementAccessPatternAttemptReason.StaticKeepOut,
                PlacementAccessPatternAttemptReason.ForeignStaticExclusion,
            },
        }
        if self.Reason not in ExpectedReasons[self.Status]:
            raise ValueError("pin-access pattern attempt status and reason disagree")
        HasOption = self.OptionFingerprint is not None
        if HasOption != (
            self.Status in {
                PlacementAccessPatternAttemptStatus.Legal,
                PlacementAccessPatternAttemptStatus.Deduplicated,
            }
        ):
            raise ValueError("pin-access pattern attempt option evidence disagrees")
        if HasOption and not self.OptionFingerprint:
            raise ValueError("pin-access pattern attempt option identity is empty")

    def RankKey(self) -> tuple[object, ...]:
        return self.TemplateId, self.Layer, self.AttemptId

    def SemanticIdentity(self) -> tuple[object, ...]:
        return (
            self.AttemptId,
            self.DomainId,
            self.TemplateId,
            self.PatternFamily,
            self.TemplateFingerprint,
            self.Layer,
            self.CatalogVersion,
            self.TechnologyFingerprint,
            self.ResourceModelFingerprint,
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "AttemptId": self.AttemptId,
            "DomainId": self.DomainId,
            "TemplateId": self.TemplateId,
            "PatternFamily": self.PatternFamily,
            "TemplateFingerprint": self.TemplateFingerprint,
            "Layer": self.Layer,
            "CatalogVersion": self.CatalogVersion,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "ResourceModelFingerprint": self.ResourceModelFingerprint,
            "Status": self.Status.value,
            "Reason": self.Reason.value,
            "OptionFingerprint": self.OptionFingerprint,
        }


@dataclass(frozen=True)
class PlacedPinAccessOptionDomain:
    """Complete-or-explicitly-incomplete option domain for one terminal."""

    DomainId: str
    Signal: str
    GateName: str
    Role: str
    PinId: str
    Terminal: Position3
    Options: tuple[PlacedPinAccessOption, ...]
    Complete: bool
    IncompleteReason: str
    CatalogVersion: str
    TechnologyFingerprint: str
    ResourceModelFingerprint: str
    EnabledPatternFamilies: tuple[str, ...]
    RequiredPatternManifest: tuple[PlacedPinAccessPatternRequirement, ...]
    PatternAttempts: tuple[PlacedPinAccessPatternAttempt, ...]
    EvaluationControlsFingerprint: str
    EvaluationInputFingerprint: str
    FinalInputFingerprint: str
    GeneratedOptionCount: int
    RejectedOptionCount: int
    DeduplicatedOptionCount: int
    MaximumGenerationWork: int

    @classmethod
    def FromDictionary(cls, Value: dict[str, object]) -> PlacedPinAccessOptionDomain:
        from .PlacementAccessCodec import ReadContract
        return ReadContract(cls, Value)

    def __post_init__(self) -> None:
        if not all((
            self.DomainId,
            self.Signal,
            self.GateName,
            self.Role,
            self.PinId,
            self.CatalogVersion,
            self.TechnologyFingerprint,
            self.ResourceModelFingerprint,
        )):
            raise ValueError("placed pin-access domain requires identities")
        if type(self.Complete) is not bool:
            raise TypeError("placed pin-access domain completeness must be Boolean")
        if self.Complete == bool(self.IncompleteReason):
            raise ValueError("placed pin-access domain completeness disagrees")
        if (
            type(self.MaximumGenerationWork) is not int
            or self.MaximumGenerationWork < 1
        ):
            raise ValueError("placed pin-access generation cap must be positive")
        Counts = (
            self.GeneratedOptionCount,
            self.RejectedOptionCount,
            self.DeduplicatedOptionCount,
        )
        if any(type(Value) is not int or Value < 0 for Value in Counts):
            raise ValueError("placed pin-access domain counts cannot be negative")
        if (
            not self.EnabledPatternFamilies
            or self.EnabledPatternFamilies
            != tuple(sorted(set(self.EnabledPatternFamilies)))
            or any(
                type(Value) is not str
                or Value not in {"straight", "planar-jog"}
                for Value in self.EnabledPatternFamilies
            )
        ):
            raise ValueError("placed pin-access enabled families are invalid")
        ExpectedControlsFingerprint = (
            BuildPlacementAccessDomainControlsFingerprint(
                EnabledPatternFamilies=self.EnabledPatternFamilies,
                CatalogVersion=self.CatalogVersion,
                MaximumGenerationWork=self.MaximumGenerationWork,
            )
        )
        if self.EvaluationControlsFingerprint != ExpectedControlsFingerprint:
            raise ValueError("placed pin-access control identity mismatch")
        if not self.EvaluationInputFingerprint:
            raise ValueError("placed pin-access domain requires an input identity")
        if not self.FinalInputFingerprint:
            raise ValueError("placed pin-access domain requires a final input identity")
        IsDrift = self.IncompleteReason == "catalog-domain-input-drift"
        if IsDrift == (
            self.FinalInputFingerprint == self.EvaluationInputFingerprint
        ):
            raise ValueError("placed pin-access drift evidence disagrees")
        OrderedManifest = tuple(sorted(
            self.RequiredPatternManifest,
            key=lambda Value: Value.RankKey(),
        ))
        if self.RequiredPatternManifest != OrderedManifest:
            raise ValueError("placed pin-access required manifest must be sorted")
        ManifestAttemptIds = tuple(
            Value.AttemptId for Value in self.RequiredPatternManifest
        )
        if len(ManifestAttemptIds) != len(set(ManifestAttemptIds)):
            raise ValueError("placed pin-access required manifest repeats a pattern")
        for Requirement in self.RequiredPatternManifest:
            Requirement.__post_init__()
            if (
                Requirement.DomainId != self.DomainId
                or Requirement.CatalogVersion != self.CatalogVersion
                or Requirement.TechnologyFingerprint
                != self.TechnologyFingerprint
                or Requirement.ResourceModelFingerprint
                != self.ResourceModelFingerprint
                or Requirement.PatternFamily not in self.EnabledPatternFamilies
            ):
                raise ValueError("required pattern belongs to another access domain")
        OrderedAttempts = tuple(sorted(
            self.PatternAttempts,
            key=lambda Value: Value.RankKey(),
        ))
        if self.PatternAttempts != OrderedAttempts:
            raise ValueError("placed pin-access pattern attempts must be sorted")
        AttemptIds = tuple(Value.AttemptId for Value in self.PatternAttempts)
        if len(AttemptIds) != len(set(AttemptIds)):
            raise ValueError("placed pin-access domain repeats a pattern attempt")
        if AttemptIds != ManifestAttemptIds:
            raise ValueError("pattern attempts do not match the required manifest")
        if any(
            Attempt.SemanticIdentity() != Requirement.SemanticIdentity()
            for Requirement, Attempt in zip(
                self.RequiredPatternManifest,
                self.PatternAttempts,
                strict=True,
            )
        ):
            raise ValueError("pattern attempt disagrees with its required manifest")
        for Attempt in self.PatternAttempts:
            Attempt.__post_init__()
            if (
                Attempt.DomainId != self.DomainId
                or Attempt.CatalogVersion != self.CatalogVersion
                or Attempt.TechnologyFingerprint != self.TechnologyFingerprint
                or Attempt.ResourceModelFingerprint
                != self.ResourceModelFingerprint
                or Attempt.PatternFamily not in self.EnabledPatternFamilies
            ):
                raise ValueError("pattern attempt belongs to another access domain")
        AttemptFamilies = {
            Value.PatternFamily for Value in self.RequiredPatternManifest
        }
        if AttemptFamilies != set(self.EnabledPatternFamilies) and (
            self.IncompleteReason
            != "catalog-domain-missing-certified-patterns"
        ):
            raise ValueError("pattern attempts do not cover enabled families")
        Unfinished = {
            PlacementAccessPatternAttemptStatus.NotEvaluated,
            PlacementAccessPatternAttemptStatus.Unknown,
        }
        if self.Complete and (
            not self.PatternAttempts
            or any(Value.Status in Unfinished for Value in self.PatternAttempts)
        ):
            raise ValueError("complete access domain lacks complete pattern evidence")
        if not self.PatternAttempts and self.IncompleteReason != (
            "catalog-domain-missing-certified-patterns"
        ):
            raise ValueError("empty access evidence lacks a missing-pattern reason")
        Ordered = tuple(sorted(self.Options, key=lambda Value: Value.RankKey()))
        if self.Options != Ordered:
            raise ValueError("placed pin-access options must be canonically sorted")
        Fingerprints = tuple(
            Value.PlacedBindingFingerprint for Value in self.Options
        )
        if len(Fingerprints) != len(set(Fingerprints)):
            raise ValueError("placed pin-access domain repeats an option")
        ExpectedIdentity = (
            self.Signal,
            self.GateName,
            self.Role,
            self.PinId,
        )
        for Option in self.Options:
            if (
                Option.TerminalIdentity() != ExpectedIdentity
                or Option.Terminal != self.Terminal
                or Option.CatalogVersion != self.CatalogVersion
                or Option.TechnologyFingerprint != self.TechnologyFingerprint
                or Option.ResourceModelFingerprint
                != self.ResourceModelFingerprint
            ):
                raise ValueError("placed pin-access option belongs to another domain")
        LegalAttempts = tuple(
            Value
            for Value in self.PatternAttempts
            if Value.Status is PlacementAccessPatternAttemptStatus.Legal
        )
        RejectedAttempts = tuple(
            Value
            for Value in self.PatternAttempts
            if Value.Status is PlacementAccessPatternAttemptStatus.Rejected
        )
        DeduplicatedAttempts = tuple(
            Value
            for Value in self.PatternAttempts
            if Value.Status is PlacementAccessPatternAttemptStatus.Deduplicated
        )
        EvaluatedAttemptCount = sum(
            Value.Status is not PlacementAccessPatternAttemptStatus.NotEvaluated
            for Value in self.PatternAttempts
        )
        if EvaluatedAttemptCount > self.MaximumGenerationWork:
            raise ValueError("placed pin-access evaluated work exceeds its cap")
        if (
            self.GeneratedOptionCount != len(LegalAttempts)
            or self.RejectedOptionCount != len(RejectedAttempts)
            or self.DeduplicatedOptionCount != len(DeduplicatedAttempts)
        ):
            raise ValueError("placed pin-access counts disagree with attempts")
        LegalFingerprints = tuple(
            Value.OptionFingerprint for Value in LegalAttempts
        )
        if (
            len(LegalFingerprints) != len(set(LegalFingerprints))
            or set(LegalFingerprints) != set(Fingerprints)
            or len(LegalFingerprints) != len(Fingerprints)
        ):
            raise ValueError(
                "every option requires exactly one legal attempt"
            )
        OptionsByFingerprint = {
            Value.PlacedBindingFingerprint: Value for Value in self.Options
        }
        for Attempt in (*LegalAttempts, *DeduplicatedAttempts):
            Option = OptionsByFingerprint.get(Attempt.OptionFingerprint)
            if Option is None or (
                Attempt.TemplateId != Option.TemplateId
                or Attempt.PatternFamily != Option.PatternFamily
                or Attempt.TemplateFingerprint != Option.TemplateFingerprint
                or Attempt.Layer != Option.Layer
                or Attempt.CatalogVersion != Option.CatalogVersion
                or Attempt.TechnologyFingerprint
                != Option.TechnologyFingerprint
                or Attempt.ResourceModelFingerprint
                != Option.ResourceModelFingerprint
            ):
                raise ValueError(
                    "pattern attempt and retained option identities disagree"
                )
        if {
            Value.OptionFingerprint for Value in LegalAttempts
        } != set(Fingerprints):
            raise ValueError("placed pin-access legal attempts disagree with options")
        if any(
            Value.OptionFingerprint not in set(Fingerprints)
            for Value in DeduplicatedAttempts
        ):
            raise ValueError("deduplicated attempt has no retained option")

    @property
    def DomainFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": "placed-pin-access-option-domain-v3",
            "DomainId": self.DomainId,
            "Signal": self.Signal,
            "GateName": self.GateName,
            "Role": self.Role,
            "PinId": self.PinId,
            "Terminal": self.Terminal,
            "Options": [
                Value.PlacedBindingFingerprint for Value in self.Options
            ],
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
            "CatalogVersion": self.CatalogVersion,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "ResourceModelFingerprint": self.ResourceModelFingerprint,
            "EnabledPatternFamilies": self.EnabledPatternFamilies,
            "RequiredPatternManifest": [
                Value.ToDictionary() for Value in self.RequiredPatternManifest
            ],
            "PatternAttempts": [
                Value.ToDictionary() for Value in self.PatternAttempts
            ],
            "EvaluationControlsFingerprint": (
                self.EvaluationControlsFingerprint
            ),
            "EvaluationInputFingerprint": self.EvaluationInputFingerprint,
            "FinalInputFingerprint": self.FinalInputFingerprint,
            "GeneratedOptionCount": self.GeneratedOptionCount,
            "RejectedOptionCount": self.RejectedOptionCount,
            "DeduplicatedOptionCount": self.DeduplicatedOptionCount,
            "MaximumGenerationWork": self.MaximumGenerationWork,
        })

    def ToDictionary(self) -> dict[str, object]:
        return {
            "DomainId": self.DomainId,
            "DomainFingerprint": self.DomainFingerprint,
            "Signal": self.Signal,
            "GateName": self.GateName,
            "Role": self.Role,
            "PinId": self.PinId,
            "Terminal": list(self.Terminal),
            "Options": [Value.ToDictionary() for Value in self.Options],
            "OptionCount": len(self.Options),
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
            "CatalogVersion": self.CatalogVersion,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "ResourceModelFingerprint": self.ResourceModelFingerprint,
            "EnabledPatternFamilies": list(self.EnabledPatternFamilies),
            "RequiredPatternManifest": [
                Value.ToDictionary() for Value in self.RequiredPatternManifest
            ],
            "PatternAttempts": [
                Value.ToDictionary() for Value in self.PatternAttempts
            ],
            "EvaluationControlsFingerprint": (
                self.EvaluationControlsFingerprint
            ),
            "EvaluationInputFingerprint": self.EvaluationInputFingerprint,
            "FinalInputFingerprint": self.FinalInputFingerprint,
            "GeneratedOptionCount": self.GeneratedOptionCount,
            "RejectedOptionCount": self.RejectedOptionCount,
            "DeduplicatedOptionCount": self.DeduplicatedOptionCount,
            "MaximumGenerationWork": self.MaximumGenerationWork,
        }


def _ValidateDomainOrder(Domains: tuple[PlacedPinAccessOptionDomain, ...]) -> None:
    Ids = tuple(Domain.DomainId for Domain in Domains)
    if not isinstance(Domains, tuple) or Ids != tuple(sorted(set(Ids))):
        raise ValueError("problem domains must be sorted and unique")
    for Domain in Domains:
        Domain.__post_init__()
    if len({(Domain.CatalogVersion, Domain.TechnologyFingerprint, Domain.ResourceModelFingerprint) for Domain in Domains}) != 1:
        raise ValueError("problem domains have mixed dependencies")
    Controls = {
        (
            Domain.EnabledPatternFamilies,
            Domain.CatalogVersion,
            Domain.MaximumGenerationWork,
            Domain.EvaluationControlsFingerprint,
            Domain.EvaluationInputFingerprint,
            Domain.FinalInputFingerprint,
        )
        for Domain in Domains
    }
    if len(Controls) != 1:
        raise ValueError("problem domains have mixed evaluation controls")
    MaximumGenerationWork = Domains[0].MaximumGenerationWork
    EvaluatedWork = sum(
        Attempt.Status is not PlacementAccessPatternAttemptStatus.NotEvaluated
        for Domain in Domains
        for Attempt in Domain.PatternAttempts
    )
    if EvaluatedWork > MaximumGenerationWork:
        raise ValueError("problem exceeds global generation work cap")


def BuildPlacementAccessProblemFingerprint(Domains: tuple[PlacedPinAccessOptionDomain, ...]) -> str:
    """Reproduce the existing exact solver identity without importing a solver."""
    _ValidateDomainOrder(Domains)
    FixedDomains = []
    for Domain in Domains:
        Identity = {
            "Kind": "fixed-placement-pin-access-domain-v1",
            "DomainId": Domain.DomainId,
            "Signal": Domain.Signal,
            "Terminal": Domain.Terminal,
            "Options": [Option.ToIdentityDictionary() for Option in Domain.Options],
            "Complete": Domain.Complete,
            "IncompleteReason": Domain.IncompleteReason,
            "SourceDomainFingerprint": Domain.DomainFingerprint,
            "CatalogVersion": Domain.CatalogVersion,
            "TechnologyFingerprint": Domain.TechnologyFingerprint,
            "ResourceModelFingerprint": Domain.ResourceModelFingerprint,
        }
        Fingerprint = BuildStableFingerprint(Identity)
        Identity.pop("Kind")
        FixedDomains.append({**Identity, "DomainFingerprint": Fingerprint, "OptionCount": len(Domain.Options)})
    return BuildStableFingerprint({
        "Kind": "fixed-placement-pin-access-problem-v2",
        "Domains": FixedDomains,
        "CatalogVersions": sorted({Domain.CatalogVersion for Domain in Domains}),
        "TechnologyFingerprints": sorted({Domain.TechnologyFingerprint for Domain in Domains}),
        "ResourceModelFingerprints": sorted({Domain.ResourceModelFingerprint for Domain in Domains}),
        "SourceDomainFingerprints": sorted({Domain.DomainFingerprint for Domain in Domains}),
    })


@dataclass(frozen=True)
class SelectedPlacementPinAccessWitness:
    """Exactly one immutable selected access option for every terminal."""

    CatalogVersion: str
    TechnologyFingerprint: str
    ResourceModelFingerprint: str
    DomainFingerprints: tuple[str, ...]
    Selections: tuple[PlacedPinAccessOption, ...]
    ClaimsBySignal: tuple[tuple[str, RoutingResourceClaims], ...]
    RepeaterReservations: tuple[RoutingReservation, ...]
    Complete: bool = True
    IncompleteReason: str = ""
    SchemaVersion: str = "selected-placement-pin-access-witness-v1"
    Domains: tuple[PlacedPinAccessOptionDomain, ...] = ()

    @classmethod
    def FromDictionary(cls, Value: dict[str, object]) -> SelectedPlacementPinAccessWitness:
        from .PlacementAccessCodec import ReadContract
        Result = ReadContract(cls, Value)
        if not Result.Domains:
            raise ValueError("serialized witness requires domain evidence")
        return Result

    def __post_init__(self) -> None:
        if self.SchemaVersion != "selected-placement-pin-access-witness-v1":
            raise ValueError("unsupported selected pin-access witness schema")
        if not all((
            self.CatalogVersion,
            self.TechnologyFingerprint,
            self.ResourceModelFingerprint,
            self.SchemaVersion,
        )):
            raise ValueError("selected pin-access witness requires identities")
        if self.Complete == bool(self.IncompleteReason):
            raise ValueError("selected pin-access witness completeness disagrees")
        if self.DomainFingerprints != tuple(sorted(self.DomainFingerprints)):
            raise ValueError("selected pin-access domain identities must be sorted")
        if len(self.DomainFingerprints) != len(set(self.DomainFingerprints)):
            raise ValueError("selected pin-access witness repeats a domain")
        Ordered = tuple(sorted(
            self.Selections,
            key=lambda Value: Value.TerminalIdentity(),
        ))
        if self.Selections != Ordered:
            raise ValueError("selected pin-access options must be sorted")
        Identities = tuple(Value.TerminalIdentity() for Value in self.Selections)
        if len(Identities) != len(set(Identities)):
            raise ValueError("selected pin-access witness repeats a terminal")
        if len(self.Selections) != len(self.DomainFingerprints):
            raise ValueError("selected pin-access witness does not cover every domain")
        ExpectedClaims = []
        for Signal in sorted({Value.Signal for Value in self.Selections}):
            ExpectedClaims.append((
                Signal,
                _MergeClaims(tuple(
                    Value.Claims
                    for Value in self.Selections
                    if Value.Signal == Signal
                )),
            ))
        if self.ClaimsBySignal != tuple(ExpectedClaims):
            raise ValueError("selected pin-access aggregate claims are stale")
        ExpectedReservations = tuple(sorted(
            (
                Reservation
                for Value in self.Selections
                for Reservation in Value.RepeaterReservations
            ),
            key=lambda Value: (
                Value.Signal,
                Value.Position,
                Value.Purpose,
                str(Value.InputFacing),
            ),
        ))
        if self.RepeaterReservations != ExpectedReservations:
            raise ValueError("selected pin-access repeaters are stale")
        for Selection in self.Selections:
            if (
                Selection.CatalogVersion != self.CatalogVersion
                or Selection.TechnologyFingerprint != self.TechnologyFingerprint
                or Selection.ResourceModelFingerprint != self.ResourceModelFingerprint
            ):
                raise ValueError("selected pin-access dependencies disagree")
        if self.Domains:
            _ValidateDomainOrder(self.Domains)
            if self.DomainFingerprints != tuple(sorted(
                Domain.DomainFingerprint for Domain in self.Domains
            )) or not all(Domain.Complete for Domain in self.Domains):
                raise ValueError("selected pin-access domain evidence mismatch")
            Options = {Option.TerminalIdentity(): Option for Option in self.Selections}
            for Domain in self.Domains:
                Option = Options.get((Domain.Signal, Domain.GateName, Domain.Role, Domain.PinId))
                if Option not in Domain.Options:
                    raise ValueError("selected pin-access option is outside its domain")

    @property
    def DomainFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": "selected-pin-access-domain-set-v1",
            "DomainFingerprints": self.DomainFingerprints,
        })

    @property
    def WitnessFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": self.SchemaVersion,
            "CatalogVersion": self.CatalogVersion,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "ResourceModelFingerprint": self.ResourceModelFingerprint,
            "DomainFingerprint": self.DomainFingerprint,
            "Selections": [
                Value.PlacedBindingFingerprint for Value in self.Selections
            ],
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
        })

    @property
    def AccessLength(self) -> int:
        """Compatibility value for the current three-cell first leg."""
        Lengths = {len(Value.FirstLegNodes) for Value in self.Selections}
        return next(iter(Lengths)) if len(Lengths) == 1 else 0

    @property
    def CatalogMatched(self) -> bool:
        return True

    def FindSelection(
        self,
        Signal: str,
        GateName: str,
        Role: str,
        PinId: str,
    ) -> PlacedPinAccessOption:
        Match = next((
            Value
            for Value in self.Selections
            if Value.TerminalIdentity()
            == (Signal, GateName, Role, PinId)
        ), None)
        if Match is None:
            raise ValueError(
                "selected pin-access witness is missing "
                f"{Signal}:{GateName}:{Role}:{PinId}"
            )
        return Match

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": self.SchemaVersion,
            "WitnessFingerprint": self.WitnessFingerprint,
            "DomainFingerprint": self.DomainFingerprint,
            "DomainFingerprints": list(self.DomainFingerprints),
            "Domains": [Domain.ToDictionary() for Domain in self.Domains],
            "CatalogVersion": self.CatalogVersion,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "ResourceModelFingerprint": self.ResourceModelFingerprint,
            "AccessLength": self.AccessLength,
            "SelectionCount": len(self.Selections),
            "SignalCount": len(self.ClaimsBySignal),
            "CatalogMatched": self.CatalogMatched,
            "Selections": [Value.ToDictionary() for Value in self.Selections],
            "ClaimsBySignal": {
                Signal: _ClaimsDictionary(Claims)
                for Signal, Claims in self.ClaimsBySignal
            },
            "RepeaterReservations": [
                _ReservationDictionary(Value)
                for Value in self.RepeaterReservations
            ],
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
        }


class PlacementAccessSolveStatus(str, Enum):
    """Exact terminal classification for one placement/access problem."""

    Feasible = "Feasible"
    Unsatisfiable = "Unsatisfiable"
    Incomplete = "Incomplete"


class CurrentSelectedPlacementAccessValidationStatus(str, Enum):
    """Outcome of a fresh current selected-access re-attestation."""

    Verified = "Verified"
    Mismatch = "Mismatch"
    Unresolved = "Unresolved"


class CurrentSelectedPlacementAccessValidationReason(str, Enum):
    """Closed, non-authoritative reasons for current-access validation."""

    Current = "Current"
    TerminalBindingsMismatch = "TerminalBindingsMismatch"
    ResourceGraphTechnologyMismatch = "ResourceGraphTechnologyMismatch"
    TechnologyFingerprintMismatch = "TechnologyFingerprintMismatch"
    ResourceModelFingerprintMismatch = "ResourceModelFingerprintMismatch"
    SolveWitnessMismatch = "SolveWitnessMismatch"
    SolveDomainEvidenceMismatch = "SolveDomainEvidenceMismatch"
    SolveProblemFingerprintMismatch = "SolveProblemFingerprintMismatch"
    RequiredPatternManifestMismatch = "RequiredPatternManifestMismatch"
    DomainEvaluationInputMismatch = "DomainEvaluationInputMismatch"
    EvaluationControlsMissing = "EvaluationControlsMissing"
    EvaluationControlsMismatch = "EvaluationControlsMismatch"
    CurrentInputChangedDuringValidation = "CurrentInputChangedDuringValidation"
    IncompleteSolve = "IncompleteSolve"
    UnsatisfiableSolve = "UnsatisfiableSolve"
    MissingDomainEvidence = "MissingDomainEvidence"
    IncompleteDomainEvidence = "IncompleteDomainEvidence"


@dataclass(frozen=True)
class CurrentSelectedPlacementAccessInputIdentity:
    """Typed identity of one non-authoritative current-input observation."""

    TerminalBindingFingerprint: str
    WitnessFingerprint: str | None
    DomainFingerprint: str | None
    SolveResultFingerprint: str
    EvaluationControlsFingerprint: str | None
    WitnessCatalogVersion: str | None
    TechnologyFingerprint: str
    ResourceModelFingerprint: str
    CurrentObservationFingerprint: str
    FinalObservationFingerprint: str | None = None
    SchemaVersion: str = "current-selected-placement-access-input-v2"

    def __post_init__(self) -> None:
        if self.SchemaVersion != "current-selected-placement-access-input-v2":
            raise ValueError("unsupported current selected-access input schema")
        for Value in (
            self.TerminalBindingFingerprint,
            self.SolveResultFingerprint,
            self.TechnologyFingerprint,
            self.ResourceModelFingerprint,
            self.CurrentObservationFingerprint,
        ):
            if type(Value) is not str or not Value:
                raise ValueError("current selected-access identity requires fingerprints")
        for Value in (
            self.WitnessFingerprint,
            self.DomainFingerprint,
            self.WitnessCatalogVersion,
            self.EvaluationControlsFingerprint,
            self.FinalObservationFingerprint,
        ):
            if Value is not None and (type(Value) is not str or not Value):
                raise ValueError("optional current selected-access identity is invalid")
        Present = (
            self.WitnessFingerprint,
            self.DomainFingerprint,
            self.WitnessCatalogVersion,
        )
        if any(Value is None for Value in Present) and any(
            Value is not None for Value in Present
        ):
            raise ValueError("current selected-access witness identity is partial")

    @property
    def InputFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": self.SchemaVersion,
            "TerminalBindingFingerprint": self.TerminalBindingFingerprint,
            "WitnessFingerprint": self.WitnessFingerprint,
            "DomainFingerprint": self.DomainFingerprint,
            "SolveResultFingerprint": self.SolveResultFingerprint,
            "EvaluationControlsFingerprint": (
                self.EvaluationControlsFingerprint
            ),
            "WitnessCatalogVersion": self.WitnessCatalogVersion,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "ResourceModelFingerprint": self.ResourceModelFingerprint,
            "CurrentObservationFingerprint": self.CurrentObservationFingerprint,
            "FinalObservationFingerprint": self.FinalObservationFingerprint,
        })

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": self.SchemaVersion,
            "TerminalBindingFingerprint": self.TerminalBindingFingerprint,
            "WitnessFingerprint": self.WitnessFingerprint,
            "DomainFingerprint": self.DomainFingerprint,
            "SolveResultFingerprint": self.SolveResultFingerprint,
            "EvaluationControlsFingerprint": (
                self.EvaluationControlsFingerprint
            ),
            "WitnessCatalogVersion": self.WitnessCatalogVersion,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "ResourceModelFingerprint": self.ResourceModelFingerprint,
            "CurrentObservationFingerprint": self.CurrentObservationFingerprint,
            "FinalObservationFingerprint": self.FinalObservationFingerprint,
            "InputFingerprint": self.InputFingerprint,
        }

    @classmethod
    def FromDictionary(
        cls,
        Value: dict[str, object],
    ) -> "CurrentSelectedPlacementAccessInputIdentity":
        """Decode structural identity only; it never proves currentness or authority."""
        from .PlacementAccessCodec import ReadContract
        return ReadContract(cls, Value)


@dataclass(frozen=True)
class CurrentSelectedPlacementAccessValidation:
    """One typed receipt from a fresh, non-regenerating current validation."""

    Status: CurrentSelectedPlacementAccessValidationStatus
    Reason: CurrentSelectedPlacementAccessValidationReason
    InputIdentity: CurrentSelectedPlacementAccessInputIdentity
    AccessRegenerationCount: int = 0
    SchemaVersion: str = "current-selected-placement-access-validation-v1"

    def __post_init__(self) -> None:
        if self.SchemaVersion != "current-selected-placement-access-validation-v1":
            raise ValueError("unsupported current selected-access validation schema")
        if type(self.Status) is not CurrentSelectedPlacementAccessValidationStatus:
            raise ValueError("current selected-access validation status must be typed")
        if type(self.Reason) is not CurrentSelectedPlacementAccessValidationReason:
            raise ValueError("current selected-access validation reason must be typed")
        if type(self.InputIdentity) is not CurrentSelectedPlacementAccessInputIdentity:
            raise ValueError("current selected-access validation requires typed input")
        if type(self.AccessRegenerationCount) is not int or self.AccessRegenerationCount != 0:
            raise ValueError("current selected-access validation cannot regenerate access")
        MismatchReasons = {
            CurrentSelectedPlacementAccessValidationReason.TerminalBindingsMismatch,
            CurrentSelectedPlacementAccessValidationReason.ResourceGraphTechnologyMismatch,
            CurrentSelectedPlacementAccessValidationReason.TechnologyFingerprintMismatch,
            CurrentSelectedPlacementAccessValidationReason.ResourceModelFingerprintMismatch,
            CurrentSelectedPlacementAccessValidationReason.SolveWitnessMismatch,
            CurrentSelectedPlacementAccessValidationReason.SolveDomainEvidenceMismatch,
            CurrentSelectedPlacementAccessValidationReason.SolveProblemFingerprintMismatch,
            CurrentSelectedPlacementAccessValidationReason.RequiredPatternManifestMismatch,
            CurrentSelectedPlacementAccessValidationReason.DomainEvaluationInputMismatch,
            CurrentSelectedPlacementAccessValidationReason.EvaluationControlsMismatch,
            CurrentSelectedPlacementAccessValidationReason.CurrentInputChangedDuringValidation,
        }
        UnresolvedReasons = {
            CurrentSelectedPlacementAccessValidationReason.IncompleteSolve,
            CurrentSelectedPlacementAccessValidationReason.UnsatisfiableSolve,
            CurrentSelectedPlacementAccessValidationReason.MissingDomainEvidence,
            CurrentSelectedPlacementAccessValidationReason.IncompleteDomainEvidence,
            CurrentSelectedPlacementAccessValidationReason.EvaluationControlsMissing,
        }
        if self.Status is CurrentSelectedPlacementAccessValidationStatus.Verified:
            if self.Reason is not CurrentSelectedPlacementAccessValidationReason.Current:
                raise ValueError("verified current selected-access validation requires Current")
        elif self.Status is CurrentSelectedPlacementAccessValidationStatus.Mismatch:
            if self.Reason not in MismatchReasons:
                raise ValueError("mismatch current selected-access validation reason is invalid")
        elif self.Reason not in UnresolvedReasons:
            raise ValueError("unresolved current selected-access validation reason is invalid")
        IsChangedDuringValidation = (
            self.Reason
            is CurrentSelectedPlacementAccessValidationReason.CurrentInputChangedDuringValidation
        )
        if (
            IsChangedDuringValidation
            != (self.InputIdentity.FinalObservationFingerprint is not None)
        ):
            raise ValueError(
                "changed-current validation requires the exact final observation only"
            )

    @property
    def ValidationResultFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": self.SchemaVersion,
            "Status": self.Status.value,
            "Reason": self.Reason.value,
            "InputIdentity": self.InputIdentity.ToDictionary(),
            "AccessRegenerationCount": self.AccessRegenerationCount,
        })

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": self.SchemaVersion,
            "Status": self.Status.value,
            "Reason": self.Reason.value,
            "InputIdentity": self.InputIdentity.ToDictionary(),
            "AccessRegenerationCount": self.AccessRegenerationCount,
            "ValidationResultFingerprint": self.ValidationResultFingerprint,
        }

    @classmethod
    def FromDictionary(
        cls,
        Value: dict[str, object],
    ) -> "CurrentSelectedPlacementAccessValidation":
        """Decode structure and fingerprints only; decoded receipts have no authority."""
        from .PlacementAccessCodec import ReadContract
        return ReadContract(cls, Value)


@dataclass(frozen=True)
class PlacementAccessConflictCore:
    """One complete problem-scoped access conflict suitable for repair."""

    CoreFingerprint: str
    ProblemFingerprint: str
    DomainFingerprints: tuple[str, ...]
    SelectionLiterals: tuple[tuple[str, str], ...]
    BlockingResources: tuple[str, ...]
    Complete: bool = True
    Minimal: bool = False
    ProblemDomains: tuple[PlacedPinAccessOptionDomain, ...] = ()

    @classmethod
    def FromDictionary(cls, Value: dict[str, object]) -> PlacementAccessConflictCore:
        from .PlacementAccessCodec import ReadContract
        Result = ReadContract(cls, Value)
        if not Result.ProblemDomains:
            raise ValueError("serialized core requires problem domain evidence")
        return Result

    def __post_init__(self) -> None:
        if not self.CoreFingerprint or not self.ProblemFingerprint:
            raise ValueError("placement-access core requires identities")
        if not self.Complete:
            raise ValueError("an incomplete conflict cannot be published as a core")
        if self.DomainFingerprints != tuple(sorted(self.DomainFingerprints)):
            raise ValueError("placement-access core domains must be sorted")
        if self.SelectionLiterals != tuple(sorted(self.SelectionLiterals)):
            raise ValueError("placement-access core literals must be sorted")
        if self.BlockingResources != tuple(sorted(self.BlockingResources)):
            raise ValueError("placement-access core resources must be sorted")
        if len(set(self.DomainFingerprints)) != len(self.DomainFingerprints) or len(set(self.SelectionLiterals)) != len(self.SelectionLiterals) or len(set(self.BlockingResources)) != len(self.BlockingResources):
            raise ValueError("placement-access core repeats evidence")
        if self.ProblemDomains:
            _ValidateDomainOrder(self.ProblemDomains)
            if self.ProblemFingerprint != BuildPlacementAccessProblemFingerprint(self.ProblemDomains):
                raise ValueError("placement-access core problem identity mismatch")
            CoreDomains = tuple(Domain for Domain in self.ProblemDomains if Domain.DomainFingerprint in self.DomainFingerprints)
            if not CoreDomains or any(not Domain.Complete for Domain in CoreDomains) or tuple(sorted(Domain.DomainFingerprint for Domain in CoreDomains)) != self.DomainFingerprints:
                raise ValueError("placement-access core domain scope mismatch")
            if self.SelectionLiterals != tuple(sorted((Domain.DomainId, Option.SelectionFingerprint) for Domain in CoreDomains for Option in Domain.Options)):
                raise ValueError("placement-access core selection scope mismatch")
            if self.CoreFingerprint != BuildStableFingerprint({
                "Kind": "placement-access-conflict-core-v1",
                "ProblemFingerprint": self.ProblemFingerprint,
                "DomainFingerprints": self.DomainFingerprints,
                "SelectionLiterals": self.SelectionLiterals,
                "BlockingResources": self.BlockingResources,
                "Complete": self.Complete,
                "Minimal": self.Minimal,
            }):
                raise ValueError("placement-access core fingerprint mismatch")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "CoreFingerprint": self.CoreFingerprint,
            "ProblemFingerprint": self.ProblemFingerprint,
            "DomainFingerprints": list(self.DomainFingerprints),
            "SelectionLiterals": [list(Value) for Value in self.SelectionLiterals],
            "BlockingResources": list(self.BlockingResources),
            "Complete": self.Complete,
            "Minimal": self.Minimal,
            "ProblemDomains": [Domain.ToDictionary() for Domain in self.ProblemDomains],
        }


@dataclass(frozen=True)
class PlacementAccessSolveResult:
    """Typed result that does not confuse bounded search with infeasibility."""

    Status: PlacementAccessSolveStatus
    ProblemFingerprint: str
    ExpansionCount: int
    MaximumExpansions: int
    SearchComplete: bool
    OptimalityProven: bool
    SelectedWitness: SelectedPlacementPinAccessWitness | None = None
    ConflictCore: PlacementAccessConflictCore | None = None
    IncompleteReason: str = ""
    SchemaVersion: str = "placement-access-solve-result-v1"
    Domains: tuple[PlacedPinAccessOptionDomain, ...] = ()
    PolicyVersion: str = ""

    @classmethod
    def FromDictionary(cls, Value: dict[str, object]) -> PlacementAccessSolveResult:
        from .PlacementAccessCodec import ReadContract
        Result = ReadContract(cls, Value)
        if not Result.Domains:
            raise ValueError("serialized solve requires problem domain evidence")
        return Result

    def __post_init__(self) -> None:
        if self.SchemaVersion != "placement-access-solve-result-v1":
            raise ValueError("unsupported placement-access solve schema")
        if not isinstance(self.Status, PlacementAccessSolveStatus):
            raise ValueError("placement-access status must be typed")
        if not self.ProblemFingerprint:
            raise ValueError("placement-access solve requires a problem identity")
        if type(self.ExpansionCount) is not int or type(self.MaximumExpansions) is not int or not 0 <= self.ExpansionCount <= self.MaximumExpansions or self.MaximumExpansions < 1:
            raise ValueError("placement-access solve work values are invalid")
        if self.OptimalityProven and not self.SearchComplete:
            raise ValueError("optimality cannot be proven by incomplete search")
        if self.Status is PlacementAccessSolveStatus.Feasible:
            if self.SelectedWitness is None or self.ConflictCore is not None:
                raise ValueError("feasible placement-access result is malformed")
            if not self.SelectedWitness.Complete:
                raise ValueError("feasible access result requires a complete witness")
            if self.SearchComplete == bool(self.IncompleteReason):
                raise ValueError("feasible search completion reason disagrees")
        elif self.Status is PlacementAccessSolveStatus.Unsatisfiable:
            if (
                self.SelectedWitness is not None
                or self.ConflictCore is None
                or not self.SearchComplete
                or self.IncompleteReason
            ):
                raise ValueError("unsatisfiable placement-access result is malformed")
        elif (
            self.SelectedWitness is not None
            or self.ConflictCore is not None
            or self.SearchComplete
            or not self.IncompleteReason
        ):
            raise ValueError("incomplete placement-access result is malformed")
        if self.Domains:
            _ValidateDomainOrder(self.Domains)
            if self.ProblemFingerprint != BuildPlacementAccessProblemFingerprint(self.Domains):
                raise ValueError("placement-access solve problem identity mismatch")
            if self.SelectedWitness is not None and self.SelectedWitness.Domains != self.Domains:
                raise ValueError("placement-access solve witness domains disagree")
            if self.ConflictCore is not None and (self.ConflictCore.ProblemDomains != self.Domains or self.ConflictCore.ProblemFingerprint != self.ProblemFingerprint):
                raise ValueError("placement-access solve core domains disagree")

    @property
    def Success(self) -> bool:
        return self.Status is PlacementAccessSolveStatus.Feasible

    @property
    def AssignmentFingerprint(self) -> str:
        return (
            self.SelectedWitness.WitnessFingerprint
            if self.SelectedWitness is not None
            else ""
        )

    def ToDictionary(self) -> dict[str, object]:
        Payload = {
            "SchemaVersion": self.SchemaVersion,
            "PolicyVersion": self.PolicyVersion,
            "Status": self.Status.value,
            "ProblemFingerprint": self.ProblemFingerprint,
            "AssignmentFingerprint": self.AssignmentFingerprint,
            "ExpansionCount": self.ExpansionCount,
            "MaximumExpansions": self.MaximumExpansions,
            "SearchComplete": self.SearchComplete,
            "OptimalityProven": self.OptimalityProven,
            "Success": self.Success,
            "SelectedWitness": (
                self.SelectedWitness.ToDictionary()
                if self.SelectedWitness is not None
                else None
            ),
            "ConflictCore": (
                self.ConflictCore.ToDictionary()
                if self.ConflictCore is not None
                else None
            ),
            "IncompleteReason": self.IncompleteReason,
            "Domains": [Domain.ToDictionary() for Domain in self.Domains],
        }
        return {**Payload, "ResultFingerprint": BuildStableFingerprint(Payload)}


@dataclass(frozen=True)
class PlacementAccessCellTransform:
    GateName: str
    GateKind: str
    Origin: Position3
    Rotation: int
    MirrorX: bool
    ClusterId: str = ""

    def __post_init__(self) -> None:
        if not self.GateName or not self.GateKind:
            raise ValueError("placement-access transform requires identities")
        if self.Rotation not in {0, 90, 180, 270}:
            raise ValueError("placement-access transform rotation is invalid")

    def StructuralIdentity(self) -> tuple[object, ...]:
        return (
            self.GateName,
            self.GateKind,
            self.Origin,
            self.Rotation,
            self.MirrorX,
            self.ClusterId,
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "GateName": self.GateName,
            "GateKind": self.GateKind,
            "Origin": list(self.Origin),
            "Rotation": self.Rotation,
            "MirrorX": self.MirrorX,
            "ClusterId": self.ClusterId,
        }


@dataclass(frozen=True)
class PlacementAccessPinMapping:
    GateName: str
    Signal: str
    Role: str
    LogicalPinId: str
    PhysicalPinId: str

    def __post_init__(self) -> None:
        if not all((
            self.GateName,
            self.Signal,
            self.LogicalPinId,
            self.PhysicalPinId,
        )):
            raise ValueError("placement-access pin mapping requires identities")
        if self.Role not in {"Source", "Target"}:
            raise ValueError("placement-access pin mapping role is invalid")

    def StructuralIdentity(self) -> tuple[str, ...]:
        return (
            self.GateName,
            self.Signal,
            self.Role,
            self.LogicalPinId,
            self.PhysicalPinId,
        )

    def ToDictionary(self) -> dict[str, str]:
        return {
            "GateName": self.GateName,
            "Signal": self.Signal,
            "Role": self.Role,
            "LogicalPinId": self.LogicalPinId,
            "PhysicalPinId": self.PhysicalPinId,
        }


@dataclass(frozen=True)
class PlacementAccessBoundaryLease:
    LeaseId: str
    Signal: str
    Terminal: Position3
    BoundaryNode: Position3
    Layer: int
    SlotIndex: int
    ClaimsFingerprint: str

    def __post_init__(self) -> None:
        if not self.LeaseId or not self.Signal or not self.ClaimsFingerprint:
            raise ValueError("placement-access boundary lease requires identities")
        if self.Layer < 0 or self.SlotIndex < 0:
            raise ValueError("placement-access boundary lease index is invalid")

    def StructuralIdentity(self) -> tuple[object, ...]:
        return (
            self.LeaseId,
            self.Signal,
            self.Terminal,
            self.BoundaryNode,
            self.Layer,
            self.SlotIndex,
            self.ClaimsFingerprint,
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "LeaseId": self.LeaseId,
            "Signal": self.Signal,
            "Terminal": list(self.Terminal),
            "BoundaryNode": list(self.BoundaryNode),
            "Layer": self.Layer,
            "SlotIndex": self.SlotIndex,
            "ClaimsFingerprint": self.ClaimsFingerprint,
        }


@dataclass(frozen=True)
class PlacementAccessChannelReservation:
    ChannelId: str
    Signal: str
    Layer: int
    LaneIndex: int
    Demand: int
    Capacity: int
    ClaimsFingerprint: str

    def __post_init__(self) -> None:
        if not self.ChannelId or not self.Signal or not self.ClaimsFingerprint:
            raise ValueError("placement-access channel requires identities")
        if self.Layer < 0 or self.LaneIndex < 0:
            raise ValueError("placement-access channel index is invalid")
        if self.Demand < 0 or self.Capacity < 0 or self.Demand > self.Capacity:
            raise ValueError("placement-access channel capacity is invalid")

    def StructuralIdentity(self) -> tuple[object, ...]:
        return (
            self.ChannelId,
            self.Signal,
            self.Layer,
            self.LaneIndex,
            self.Demand,
            self.Capacity,
            self.ClaimsFingerprint,
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ChannelId": self.ChannelId,
            "Signal": self.Signal,
            "Layer": self.Layer,
            "LaneIndex": self.LaneIndex,
            "Demand": self.Demand,
            "Capacity": self.Capacity,
            "ClaimsFingerprint": self.ClaimsFingerprint,
        }


@dataclass(frozen=True)
class PlacementAccessEnvelope:
    Minimum: Position3
    Maximum: Position3

    def __post_init__(self) -> None:
        if any(
            self.Minimum[Index] > self.Maximum[Index]
            for Index in range(3)
        ):
            raise ValueError("placement-access envelope bounds are inverted")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Minimum": list(self.Minimum),
            "Maximum": list(self.Maximum),
        }


@dataclass(frozen=True)
class FrozenPhysicalPlacementContract:
    """Portable selected placement/access handoff for authoritative routing."""

    ModuleFingerprint: str
    PolicyVersion: str
    CatalogVersion: str
    TechnologyFingerprint: str
    ResourceModelFingerprint: str
    ProblemFingerprint: str
    ProofFingerprint: str
    CellTransforms: tuple[PlacementAccessCellTransform, ...]
    PinMappings: tuple[PlacementAccessPinMapping, ...]
    SelectedPinAccessWitness: SelectedPlacementPinAccessWitness
    BoundaryLeases: tuple[PlacementAccessBoundaryLease, ...]
    ChannelReservations: tuple[PlacementAccessChannelReservation, ...]
    Envelope: PlacementAccessEnvelope
    DomainComplete: bool
    SearchComplete: bool
    OptimalityProven: bool
    SchemaVersion: str = "frozen-physical-placement-contract-v1"

    def __post_init__(self) -> None:
        if not all((
            self.ModuleFingerprint,
            self.PolicyVersion,
            self.CatalogVersion,
            self.TechnologyFingerprint,
            self.ResourceModelFingerprint,
            self.ProblemFingerprint,
            self.ProofFingerprint,
            self.SchemaVersion,
        )):
            raise ValueError("frozen placement contract requires identities")
        if self.OptimalityProven and not self.SearchComplete:
            raise ValueError("frozen placement optimality requires complete search")
        if not self.DomainComplete:
            raise ValueError("an incomplete domain cannot be frozen for routing")
        if not self.SelectedPinAccessWitness.Complete:
            raise ValueError("frozen placement requires a complete access witness")
        if (
            self.CatalogVersion
            != self.SelectedPinAccessWitness.CatalogVersion
            or self.TechnologyFingerprint
            != self.SelectedPinAccessWitness.TechnologyFingerprint
            or self.ResourceModelFingerprint
            != self.SelectedPinAccessWitness.ResourceModelFingerprint
        ):
            raise ValueError("frozen placement and access identities disagree")
        if self.CellTransforms != tuple(sorted(
            self.CellTransforms,
            key=lambda Value: Value.StructuralIdentity(),
        )):
            raise ValueError("frozen placement cell transforms must be sorted")
        if len({
            Value.GateName for Value in self.CellTransforms
        }) != len(self.CellTransforms):
            raise ValueError("frozen placement repeats a cell transform")
        if self.PinMappings != tuple(sorted(
            self.PinMappings,
            key=lambda Value: Value.StructuralIdentity(),
        )):
            raise ValueError("frozen placement pin mappings must be sorted")
        if len({
            Value.StructuralIdentity() for Value in self.PinMappings
        }) != len(self.PinMappings):
            raise ValueError("frozen placement repeats a pin mapping")
        if self.BoundaryLeases != tuple(sorted(
            self.BoundaryLeases,
            key=lambda Value: Value.StructuralIdentity(),
        )):
            raise ValueError("frozen placement boundary leases must be sorted")
        if len({
            Value.LeaseId for Value in self.BoundaryLeases
        }) != len(self.BoundaryLeases):
            raise ValueError("frozen placement repeats a boundary lease")
        if self.ChannelReservations != tuple(sorted(
            self.ChannelReservations,
            key=lambda Value: Value.StructuralIdentity(),
        )):
            raise ValueError("frozen placement channel reservations must be sorted")
        if len({
            (Value.ChannelId, Value.Layer, Value.LaneIndex)
            for Value in self.ChannelReservations
        }) != len(self.ChannelReservations):
            raise ValueError("frozen placement repeats a channel lane")
        TransformGateNames = {
            Value.GateName for Value in self.CellTransforms
        }
        WitnessGateNames = {
            Value.GateName
            for Value in self.SelectedPinAccessWitness.Selections
        }
        if not WitnessGateNames.issubset(TransformGateNames):
            raise ValueError("frozen placement is missing a selected gate transform")
        MappingTerminals = {
            (
                Value.GateName,
                Value.Signal,
                Value.Role,
                Value.PhysicalPinId,
            )
            for Value in self.PinMappings
        }
        WitnessTerminals = {
            (
                Value.GateName,
                Value.Signal,
                Value.Role,
                Value.PinId,
            )
            for Value in self.SelectedPinAccessWitness.Selections
        }
        if MappingTerminals != WitnessTerminals:
            raise ValueError("frozen placement pin mappings do not cover its witness")

    @property
    def ContractFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": self.SchemaVersion,
            "ModuleFingerprint": self.ModuleFingerprint,
            "PolicyVersion": self.PolicyVersion,
            "CatalogVersion": self.CatalogVersion,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "ResourceModelFingerprint": self.ResourceModelFingerprint,
            "ProblemFingerprint": self.ProblemFingerprint,
            "ProofFingerprint": self.ProofFingerprint,
            "CellTransforms": [
                Value.StructuralIdentity() for Value in self.CellTransforms
            ],
            "PinMappings": [
                Value.StructuralIdentity() for Value in self.PinMappings
            ],
            "SelectedPinAccessWitnessFingerprint": (
                self.SelectedPinAccessWitness.WitnessFingerprint
            ),
            "BoundaryLeases": [
                Value.StructuralIdentity() for Value in self.BoundaryLeases
            ],
            "ChannelReservations": [
                Value.StructuralIdentity()
                for Value in self.ChannelReservations
            ],
            "Envelope": self.Envelope.ToDictionary(),
            "DomainComplete": self.DomainComplete,
            "SearchComplete": self.SearchComplete,
            "OptimalityProven": self.OptimalityProven,
        })

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": self.SchemaVersion,
            "ContractFingerprint": self.ContractFingerprint,
            "ModuleFingerprint": self.ModuleFingerprint,
            "PolicyVersion": self.PolicyVersion,
            "CatalogVersion": self.CatalogVersion,
            "TechnologyFingerprint": self.TechnologyFingerprint,
            "ResourceModelFingerprint": self.ResourceModelFingerprint,
            "ProblemFingerprint": self.ProblemFingerprint,
            "ProofFingerprint": self.ProofFingerprint,
            "CellTransforms": [
                Value.ToDictionary() for Value in self.CellTransforms
            ],
            "PinMappings": [
                Value.ToDictionary() for Value in self.PinMappings
            ],
            "SelectedPinAccessWitness": (
                self.SelectedPinAccessWitness.ToDictionary()
            ),
            "BoundaryLeases": [
                Value.ToDictionary() for Value in self.BoundaryLeases
            ],
            "ChannelReservations": [
                Value.ToDictionary() for Value in self.ChannelReservations
            ],
            "Envelope": self.Envelope.ToDictionary(),
            "DomainComplete": self.DomainComplete,
            "SearchComplete": self.SearchComplete,
            "OptimalityProven": self.OptimalityProven,
        }


__all__ = [
    "BuildPlacementAccessDomainControlsFingerprint",
    "BuildPlacementAccessPatternAttemptId",
    "CurrentSelectedPlacementAccessInputIdentity",
    "CurrentSelectedPlacementAccessValidation",
    "CurrentSelectedPlacementAccessValidationReason",
    "CurrentSelectedPlacementAccessValidationStatus",
    "FrozenPhysicalPlacementContract",
    "PhysicalPinAccessTemplate",
    "PlacedPinAccessOption",
    "PlacedPinAccessOptionDomain",
    "PlacedPinAccessPatternAttempt",
    "PlacedPinAccessPatternRequirement",
    "PlacementAccessBoundaryLease",
    "PlacementAccessCellTransform",
    "PlacementAccessChannelReservation",
    "PlacementAccessConflictCore",
    "PlacementAccessEnvelope",
    "PlacementAccessEvaluationControls",
    "PlacementAccessPinMapping",
    "PlacementAccessPatternAttemptReason",
    "PlacementAccessPatternAttemptStatus",
    "PlacementAccessSolveResult",
    "PlacementAccessSolveStatus",
    "SelectedPlacementPinAccessWitness",
]
