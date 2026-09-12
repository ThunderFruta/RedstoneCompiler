"""One bounded authoritative track-assignment selection across templates.

Placement alternatives own mutually exclusive resource graphs.  They cannot
be flattened into one ordinary assignment because that would require every
placement to route simultaneously.  This module instead presents those raw
domains as one deterministic selection problem: a complete capacity core for
one template permits the next fixed template to be considered, while work or
deadline exhaustion terminates the whole problem as incomplete.

The existing Rust ``RoutingContext`` assignment binding remains authoritative
for each raw physical domain.  The aggregate selector carries one immutable
work counter and one absolute deadline across those calls; it is not a retry
or a route attempt.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum
from math import isfinite
from types import MappingProxyType, SimpleNamespace
from typing import Any, Callable, Iterable, Mapping

from ..Global.Orchestration.RunModels import RawTrackAssignmentDomain
from ..Global.Assignment.TrackPortfolio import BuildTrackAssignmentPreparationFromRawDomain
from ...Contracts.Placement import TrackAssignmentPreparation
from ...Runtime.Reliability import BuildStableFingerprint, RoutingDeadline


def _RequireExactBoolean(Value: object, Name: str) -> bool:
    if type(Value) is not bool:
        raise TypeError(f"{Name} must be an exact bool")
    return Value


def _RequireExactNonBooleanInteger(
    Value: object,
    Name: str,
    *,
    Minimum: int = 0,
) -> int:
    if type(Value) is not int:
        raise TypeError(f"{Name} must be an exact non-Boolean int")
    if Value < Minimum:
        raise ValueError(f"{Name} must be at least {Minimum}")
    return Value


def _RequireExactIntegerTuple(
    Value: object,
    Name: str,
) -> tuple[int, ...]:
    if type(Value) is not tuple:
        raise TypeError(f"{Name} must be an exact tuple")
    for Index, Item in enumerate(Value):
        _RequireExactNonBooleanInteger(Item, f"{Name}[{Index}]")
    return Value


def _RequireExactStringPairTuple(
    Value: object,
    Name: str,
) -> tuple[tuple[str, str], ...]:
    if type(Value) is not tuple or any(
        type(Item) is not tuple
        or len(Item) != 2
        or type(Item[0]) is not str
        or not Item[0]
        or type(Item[1]) is not str
        or not Item[1]
        for Item in Value
    ):
        raise TypeError(f"{Name} must contain exact nonempty string pairs")
    return Value


def _FreezeCandidateAuthorityValue(
    Value: object,
    *,
    Path: str,
    Active: set[int] | None = None,
) -> object:
    """Deeply detach authority into an explicitly tagged immutable tree."""
    if Value is None:
        return ("Null",)
    if type(Value) is bool:
        return ("Boolean", Value)
    if type(Value) is int:
        return ("Integer", Value)
    if type(Value) is str:
        return ("String", Value)
    if type(Value) is float:
        if not isfinite(Value):
            raise TypeError(f"{Path} contains a non-finite float")
        return ("Float", Value)
    if isinstance(Value, Enum):
        return _FreezeCandidateAuthorityValue(
            Value.value,
            Path=Path,
            Active=Active,
        )
    if Active is None:
        Active = set()
    Identity = id(Value)
    if Identity in Active:
        raise TypeError(f"{Path} contains a cycle")
    Active.add(Identity)
    try:
        if isinstance(Value, Mapping):
            Keys = tuple(Value)
            if any(type(Key) is not str or not Key for Key in Keys):
                raise TypeError(
                    f"{Path} requires nonempty exact string keys"
                )
            return (
                "Map",
                tuple(
                    (
                        Key,
                        _FreezeCandidateAuthorityValue(
                            Value[Key],
                            Path=f"{Path}.{Key}",
                            Active=Active,
                        ),
                    )
                    for Key in sorted(Keys)
                ),
            )
        if type(Value) in (tuple, list):
            return (
                "Sequence",
                tuple(
                    _FreezeCandidateAuthorityValue(
                        Item,
                        Path=f"{Path}[{Index}]",
                        Active=Active,
                    )
                    for Index, Item in enumerate(Value)
                ),
            )
        if type(Value) in (set, frozenset):
            Frozen = tuple(
                _FreezeCandidateAuthorityValue(
                    Item,
                    Path=f"{Path}[]",
                    Active=Active,
                )
                for Item in Value
            )
            return (
                "Set",
                tuple(sorted(
                    Frozen,
                    key=_CandidateAuthorityCanonicalText,
                )),
            )
    finally:
        Active.remove(Identity)
    raise TypeError(f"{Path} contains unsupported {type(Value).__name__}")


def _SerializeFrozenCandidateAuthority(Value: object) -> dict[str, object]:
    if type(Value) is not tuple or not Value or type(Value[0]) is not str:
        raise TypeError("candidate authority node must be an exact tagged tuple")
    Kind = Value[0]
    if Kind == "Null":
        if len(Value) != 1:
            raise TypeError("Null authority node is malformed")
        return {"Kind": "Null"}
    if Kind in {"Boolean", "Integer", "Float", "String"}:
        if len(Value) != 2:
            raise TypeError(f"{Kind} authority node is malformed")
        Scalar = Value[1]
        ExpectedType = {
            "Boolean": bool,
            "Integer": int,
            "Float": float,
            "String": str,
        }[Kind]
        if type(Scalar) is not ExpectedType:
            raise TypeError(f"{Kind} authority node has the wrong scalar type")
        if Kind == "Float" and not isfinite(Scalar):
            raise TypeError("Float authority node is not finite")
        return {"Kind": Kind, "Value": Scalar}
    if Kind == "Map":
        if len(Value) != 2 or type(Value[1]) is not tuple:
            raise TypeError("Map authority node is malformed")
        Entries = Value[1]
        if any(
            type(Item) is not tuple
            or len(Item) != 2
            or type(Item[0]) is not str
            or not Item[0]
            for Item in Entries
        ):
            raise TypeError("Map authority entries are malformed")
        Keys = tuple(Item[0] for Item in Entries)
        if Keys != tuple(sorted(set(Keys))):
            raise ValueError("Map authority keys are not unique and sorted")
        return {
            "Kind": "Map",
            "Entries": [
                {
                    "Key": Key,
                    "Value": _SerializeFrozenCandidateAuthority(Item),
                }
                for Key, Item in Entries
            ],
        }
    if Kind in {"Sequence", "Set"}:
        if len(Value) != 2 or type(Value[1]) is not tuple:
            raise TypeError(f"{Kind} authority node is malformed")
        Serialized = [
            _SerializeFrozenCandidateAuthority(Item)
            for Item in Value[1]
        ]
        if Kind == "Set":
            Canonical = tuple(
                _CandidateAuthorityCanonicalText(Item)
                for Item in Value[1]
            )
            if Canonical != tuple(sorted(set(Canonical))):
                raise ValueError("Set authority items are not unique and sorted")
        return {"Kind": Kind, "Items": Serialized}
    raise TypeError(f"unknown candidate authority node kind: {Kind}")


def _CandidateAuthorityCanonicalText(Value: object) -> str:
    import json

    return json.dumps(
        _SerializeFrozenCandidateAuthority(Value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _FreezePreparationDiagnosticValue(
    Value: object,
    *,
    Path: str,
    Active: set[int] | None = None,
) -> object:
    """Detach diagnostics without changing scalar or mapping semantics."""
    if Value is None or type(Value) in (bool, int, str):
        return Value
    if type(Value) is float:
        if not isfinite(Value):
            raise TypeError(f"{Path} contains a non-finite float")
        return Value
    if isinstance(Value, Enum):
        return Value.value
    if Active is None:
        Active = set()
    Identity = id(Value)
    if Identity in Active:
        raise TypeError(f"{Path} contains a cycle")
    Active.add(Identity)
    try:
        if isinstance(Value, Mapping):
            Keys = tuple(Value)
            if any(type(Key) is not str or not Key for Key in Keys):
                raise TypeError(
                    f"{Path} requires nonempty exact string keys"
                )
            return MappingProxyType({
                Key: _FreezePreparationDiagnosticValue(
                    Value[Key],
                    Path=f"{Path}.{Key}",
                    Active=Active,
                )
                for Key in sorted(Keys)
            })
        if type(Value) in (tuple, list):
            return tuple(
                _FreezePreparationDiagnosticValue(
                    Item,
                    Path=f"{Path}[{Index}]",
                    Active=Active,
                )
                for Index, Item in enumerate(Value)
            )
        if type(Value) in (set, frozenset):
            return frozenset(
                _FreezePreparationDiagnosticValue(
                    Item,
                    Path=f"{Path}[]",
                    Active=Active,
                )
                for Item in Value
            )
    finally:
        Active.remove(Identity)
    raise TypeError(f"{Path} contains unsupported {type(Value).__name__}")


@dataclass(frozen=True)
class RawTrackAssignmentCandidateInputManifest:
    """Canonical, transparent input dependencies for one lazy candidate."""

    Payload: object
    SchemaVersion: str = "raw-track-assignment-candidate-input-v1"

    def __post_init__(self) -> None:
        if self.SchemaVersion != "raw-track-assignment-candidate-input-v1":
            raise ValueError("unsupported candidate input manifest schema")
        Serialized = _SerializeFrozenCandidateAuthority(self.Payload)
        if Serialized.get("Kind") != "Map":
            raise TypeError("candidate input manifest root must be a Map")

    @classmethod
    def Capture(
        cls,
        Value: Mapping[str, object],
    ) -> "RawTrackAssignmentCandidateInputManifest":
        if type(Value) is not dict:
            raise TypeError("candidate input manifest source must be exact dict")
        Payload = _FreezeCandidateAuthorityValue(
            Value,
            Path="CandidateInput",
        )
        return cls(Payload=Payload)

    @property
    def ManifestFingerprint(self) -> str:
        return BuildStableFingerprint({
            "SchemaVersion": self.SchemaVersion,
            "Payload": _SerializeFrozenCandidateAuthority(self.Payload),
        })

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": self.SchemaVersion,
            "ManifestFingerprint": self.ManifestFingerprint,
            "Payload": _SerializeFrozenCandidateAuthority(self.Payload),
        }


def BuildRawTrackAssignmentWorkControlsFingerprint(
    MaximumAssignmentExpansions: int,
    Deadline: RoutingDeadline | None = None,
) -> str:
    """Bind candidate results to the exact selector work authority.

    Pure selector tests may omit a deadline and therefore bind only the
    expansion cap.  The production context wrapper always supplies its one
    caller-owned absolute deadline, including the original monotonic bounds.
    """
    _RequireExactNonBooleanInteger(
        MaximumAssignmentExpansions,
        "MaximumAssignmentExpansions",
        Minimum=1,
    )
    if Deadline is not None and type(Deadline) is not RoutingDeadline:
        raise TypeError("Deadline must be an exact RoutingDeadline")
    DeadlineIdentity = (
        {
            "StartedAt": Deadline.StartedAt,
            "ExpiresAt": Deadline.ExpiresAt,
            "ExpirationKind": Deadline.ExpirationKind,
        }
        if Deadline is not None
        else None
    )
    return BuildStableFingerprint({
        "Kind": "raw-track-assignment-work-controls-v1",
        "MaximumAssignmentExpansions": MaximumAssignmentExpansions,
        "Deadline": DeadlineIdentity,
    })


def _TrackAssignmentPreparationAuthorityPayload(
    Preparation: TrackAssignmentPreparation,
) -> dict[str, object]:
    """Preserve preparation pair sequences without dictionary inference."""
    return {
        "Success": Preparation.Success,
        "SelectedCandidateIds": Preparation.SelectedCandidateIds,
        "CandidateCounts": Preparation.CandidateCounts,
        "ConflictSignals": Preparation.ConflictSignals,
        "ConflictResourceIndices": Preparation.ConflictResourceIndices,
        "ExpansionCount": Preparation.ExpansionCount,
        "Complete": Preparation.Complete,
        "IncompleteReason": Preparation.IncompleteReason,
        "Diagnostics": Preparation.Diagnostics,
        "SelectedLocalClaimChoiceIds": (
            Preparation.SelectedLocalClaimChoiceIds
        ),
        "LocalClaimDomainFingerprint": (
            Preparation.LocalClaimDomainFingerprint
        ),
        "CandidateDomainFingerprint": (
            Preparation.CandidateDomainFingerprint
        ),
        "SelectedCapacityResourceIds": (
            Preparation.SelectedCapacityResourceIds
        ),
        "PinAccessDomainFingerprint": (
            Preparation.PinAccessDomainFingerprint
        ),
        "PinAccessWitnessFingerprint": (
            Preparation.PinAccessWitnessFingerprint
        ),
        "PinAccessHandoffObservation": (
            Preparation.PinAccessHandoffObservation.ToDictionary()
            if Preparation.PinAccessHandoffObservation is not None
            else None
        ),
    }


@dataclass(frozen=True)
class RawTrackAssignmentTemplate:
    """One immutable, mutually exclusive authoritative assignment domain."""

    TemplateId: str
    Objective: tuple[int, ...]
    Domain: RawTrackAssignmentDomain

    def __post_init__(self) -> None:
        if type(self.TemplateId) is not str or not self.TemplateId:
            raise ValueError("raw track-assignment template requires an id")
        _RequireExactIntegerTuple(self.Objective, "Objective")
        if type(self.Domain) is not RawTrackAssignmentDomain:
            raise TypeError("raw track-assignment domain must be exact")
        _RequireExactBoolean(self.Domain.Complete, "Domain.Complete")
        _RequireExactNonBooleanInteger(
            self.Domain.MaximumAssignmentExpansions,
            "Domain.MaximumAssignmentExpansions",
            Minimum=1,
        )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "TemplateId": self.TemplateId,
            "Objective": list(self.Objective),
            "Domain": self.Domain.ToDictionary(),
        }


@dataclass(frozen=True)
class RawTrackAssignmentProblem:
    """Fixed finite placement/template capacity problem.

    ``MaximumAssignmentExpansions`` is global to the problem.  Every member
    must carry the same declared cap so adding a geometry member cannot turn
    the cap into an accidental per-template budget multiplier.
    """

    Templates: tuple[RawTrackAssignmentTemplate, ...]
    MaximumAssignmentExpansions: int
    NonExhaustiveTemplateDomain: bool = True

    def __post_init__(self) -> None:
        if type(self.Templates) is not tuple or any(
            type(Value) is not RawTrackAssignmentTemplate
            for Value in self.Templates
        ):
            raise TypeError("raw template assignment members must be exact")
        _RequireExactNonBooleanInteger(
            self.MaximumAssignmentExpansions,
            "MaximumAssignmentExpansions",
            Minimum=1,
        )
        _RequireExactBoolean(
            self.NonExhaustiveTemplateDomain,
            "NonExhaustiveTemplateDomain",
        )
        TemplateIds = tuple(Value.TemplateId for Value in self.Templates)
        if len(TemplateIds) != len(set(TemplateIds)):
            raise ValueError("raw template assignment repeats a template id")
        MismatchedCaps = tuple(
            Value.TemplateId
            for Value in self.Templates
            if Value.Domain.MaximumAssignmentExpansions
            != self.MaximumAssignmentExpansions
        )
        if MismatchedCaps:
            raise ValueError(
                "raw template assignment members must share one work cap: "
                + ", ".join(MismatchedCaps)
            )
        if not self.NonExhaustiveTemplateDomain:
            TruncatedTemplates = tuple(
                Value.TemplateId
                for Value in self.Templates
                if bool(dict(Value.Domain.Diagnostics).get(
                    "ExcludedConfiguredRequestCounts",
                    (),
                ))
            )
            if TruncatedTemplates:
                raise ValueError(
                    "a raw template with excluded configured request shapes "
                    "cannot be declared exhaustive: "
                    + ", ".join(TruncatedTemplates)
                )

    @property
    def ProblemFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": "raw-template-track-assignment-v1",
            "Templates": [
                Value.ToDictionary()
                for Value in sorted(
                    self.Templates,
                    key=lambda Value: (Value.Objective, Value.TemplateId),
                )
            ],
            "MaximumAssignmentExpansions": (
                self.MaximumAssignmentExpansions
            ),
            "NonExhaustiveTemplateDomain": (
                self.NonExhaustiveTemplateDomain
            ),
        })

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ProblemFingerprint": self.ProblemFingerprint,
            "TemplateCount": len(self.Templates),
            "MaximumAssignmentExpansions": (
                self.MaximumAssignmentExpansions
            ),
            "NonExhaustiveTemplateDomain": (
                self.NonExhaustiveTemplateDomain
            ),
        }


@dataclass(frozen=True)
class RawTrackAssignmentPortfolioTemplate:
    """A fixed raw-template input whose domain is materialized on demand.

    ``Objective`` is an immutable selection prefix known before materializing
    the raw domain.  Most callers provide the complete objective.  A
    placement/access portfolio may instead provide its exact geometry/layer
    prefix, then report the remaining material/access terms with the typed
    materialization.  In that form, all descriptors sharing the prefix are
    materialized before the selector chooses their resolved full objective.
    """

    TemplateId: str
    Objective: tuple[int, ...]
    MaterializationInputFingerprint: str
    MaterializationInputManifest: RawTrackAssignmentCandidateInputManifest

    def __post_init__(self) -> None:
        if type(self.TemplateId) is not str or not self.TemplateId:
            raise ValueError("raw track-assignment portfolio requires an id")
        _RequireExactIntegerTuple(self.Objective, "Objective")
        if (
            type(self.MaterializationInputFingerprint) is not str
            or not self.MaterializationInputFingerprint
        ):
            raise ValueError(
                "raw track-assignment portfolio requires an input fingerprint"
            )
        if (
            type(self.MaterializationInputManifest)
            is not RawTrackAssignmentCandidateInputManifest
        ):
            raise TypeError(
                "raw track-assignment portfolio requires an exact input manifest"
            )
        if (
            self.MaterializationInputFingerprint
            != self.MaterializationInputManifest.ManifestFingerprint
        ):
            raise ValueError(
                "raw track-assignment portfolio input fingerprint mismatches "
                "its manifest"
            )

    def ToDictionary(self) -> dict[str, object]:
        return {
            "TemplateId": self.TemplateId,
            "Objective": list(self.Objective),
            "MaterializationInputFingerprint": (
                self.MaterializationInputFingerprint
            ),
            "MaterializationInputManifest": (
                self.MaterializationInputManifest.ToDictionary()
            ),
        }


@dataclass(frozen=True)
class RawTrackAssignmentPortfolio:
    """One fixed, lazily materialized authoritative template portfolio.

    Laziness is limited to deterministic construction of already-declared
    members.  It never adds geometry, changes a policy, or schedules a new
    routing attempt.  A selected member proves that all unmaterialized
    members sort strictly after it by their immutable objective.
    """

    Templates: tuple[RawTrackAssignmentPortfolioTemplate, ...]
    MaximumAssignmentExpansions: int
    WorkControlsFingerprint: str = ""
    NonExhaustiveTemplateDomain: bool = True
    _ProblemFingerprint: str = field(
        init=False,
        repr=False,
        compare=True,
    )

    def __post_init__(self) -> None:
        if type(self.Templates) is not tuple or any(
            type(Value) is not RawTrackAssignmentPortfolioTemplate
            for Value in self.Templates
        ):
            raise TypeError("raw template portfolio members must be exact")
        _RequireExactNonBooleanInteger(
            self.MaximumAssignmentExpansions,
            "MaximumAssignmentExpansions",
            Minimum=1,
        )
        _RequireExactBoolean(
            self.NonExhaustiveTemplateDomain,
            "NonExhaustiveTemplateDomain",
        )
        if type(self.WorkControlsFingerprint) is not str:
            raise TypeError("WorkControlsFingerprint must be an exact string")
        if not self.WorkControlsFingerprint:
            object.__setattr__(
                self,
                "WorkControlsFingerprint",
                BuildRawTrackAssignmentWorkControlsFingerprint(
                    self.MaximumAssignmentExpansions
                ),
            )
        TemplateIds = tuple(Value.TemplateId for Value in self.Templates)
        if len(TemplateIds) != len(set(TemplateIds)):
            raise ValueError("raw template portfolio repeats a template id")
        object.__setattr__(
            self,
            "_ProblemFingerprint",
            BuildStableFingerprint({
                "Kind": "raw-template-track-assignment-portfolio-v1",
                "Templates": [
                    Value.ToDictionary()
                    for Value in sorted(
                        self.Templates,
                        key=lambda Value: (
                            Value.Objective,
                            Value.TemplateId,
                        ),
                    )
                ],
                "MaximumAssignmentExpansions": (
                    self.MaximumAssignmentExpansions
                ),
                "NonExhaustiveTemplateDomain": (
                    self.NonExhaustiveTemplateDomain
                ),
            }),
        )

    @property
    def ProblemFingerprint(self) -> str:
        """Return the immutable identity captured before lazy materialization."""
        return self._ProblemFingerprint

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ProblemFingerprint": self.ProblemFingerprint,
            "TemplateCount": len(self.Templates),
            "MaximumAssignmentExpansions": (
                self.MaximumAssignmentExpansions
            ),
            "WorkControlsFingerprint": self.WorkControlsFingerprint,
            "NonExhaustiveTemplateDomain": (
                self.NonExhaustiveTemplateDomain
            ),
        }


@dataclass(frozen=True)
class RawTrackAssignmentMaterialization:
    """Typed result of constructing one predeclared raw template domain."""

    TemplateId: str
    MaterializationInputFingerprint: str
    MaterializationInputManifest: RawTrackAssignmentCandidateInputManifest
    Domain: RawTrackAssignmentDomain | None
    Complete: bool
    IncompleteReason: str = ""
    Diagnostics: tuple[tuple[str, object], ...] = ()
    ResolvedObjective: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if type(self.TemplateId) is not str or not self.TemplateId:
            raise ValueError("raw template materialization requires an id")
        if (
            type(self.MaterializationInputFingerprint) is not str
            or not self.MaterializationInputFingerprint
        ):
            raise ValueError(
                "raw template materialization requires its input identity"
            )
        if (
            type(self.MaterializationInputManifest)
            is not RawTrackAssignmentCandidateInputManifest
        ):
            raise TypeError(
                "raw template materialization requires an exact input manifest"
            )
        if (
            self.MaterializationInputFingerprint
            != self.MaterializationInputManifest.ManifestFingerprint
        ):
            raise ValueError(
                "raw template materialization input fingerprint mismatches "
                "its manifest"
            )
        _RequireExactBoolean(self.Complete, "Materialization.Complete")
        if self.Domain is not None:
            if type(self.Domain) is not RawTrackAssignmentDomain:
                raise TypeError("materialization domain must be exact")
            _RequireExactBoolean(self.Domain.Complete, "Domain.Complete")
            _RequireExactNonBooleanInteger(
                self.Domain.MaximumAssignmentExpansions,
                "Domain.MaximumAssignmentExpansions",
                Minimum=1,
            )
        if self.Complete != (self.Domain is not None and self.Domain.Complete):
            raise ValueError(
                "raw template materialization completeness must match its "
                "domain"
            )
        if type(self.IncompleteReason) is not str:
            raise TypeError(
                "materialization incomplete reason must be an exact string"
            )
        if not self.Complete and not self.IncompleteReason:
            raise ValueError(
                "incomplete raw template materialization requires a reason"
            )
        _RequireExactIntegerTuple(
            self.ResolvedObjective,
            "Materialization.ResolvedObjective",
        )
        if type(self.Diagnostics) is not tuple or any(
            type(Item) is not tuple
            or len(Item) != 2
            or type(Item[0]) is not str
            for Item in self.Diagnostics
        ):
            raise TypeError("materialization diagnostics must be exact pairs")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "TemplateId": self.TemplateId,
            "MaterializationInputFingerprint": (
                self.MaterializationInputFingerprint
            ),
            "MaterializationInputManifest": (
                self.MaterializationInputManifest.ToDictionary()
            ),
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
            "Domain": (
                self.Domain.ToDictionary()
                if self.Domain is not None
                else None
            ),
            "Diagnostics": dict(self.Diagnostics),
            "ResolvedObjective": list(self.ResolvedObjective),
        }

    def ToBoundedFailureDictionary(self) -> dict[str, object]:
        """Project one materialization without replaying its frozen inputs."""
        Domain = self.Domain
        return {
            "TemplateId": self.TemplateId,
            "CandidateInputFingerprint": (
                self.MaterializationInputFingerprint
            ),
            "Complete": self.Complete,
            "IncompleteReason": self.IncompleteReason,
            "ResolvedObjective": list(self.ResolvedObjective),
            "Domain": {
                "Present": Domain is not None,
                "Complete": Domain.Complete if Domain is not None else False,
                "ResourceCount": (
                    len(Domain.ResourcePositions) if Domain is not None else 0
                ),
                "ValueCount": len(Domain.Values) if Domain is not None else 0,
                "CandidateCounts": (
                    [list(Value) for Value in Domain.CandidateCounts]
                    if Domain is not None
                    else []
                ),
                "CandidateDomainFingerprint": (
                    Domain.CandidateDomainFingerprint
                    if Domain is not None
                    else ""
                ),
                "LocalClaimDomainFingerprint": (
                    Domain.LocalClaimDomainFingerprint
                    if Domain is not None
                    else ""
                ),
                "PlacementFingerprint": (
                    Domain.PlacementFingerprint if Domain is not None else ""
                ),
                "ResourceGraphFingerprint": (
                    Domain.ResourceGraphFingerprint
                    if Domain is not None
                    else ""
                ),
                "PortalDomainFingerprint": (
                    Domain.PortalDomainFingerprint
                    if Domain is not None
                    else ""
                ),
            },
            "EvidenceCompleteness": {
                "FullInputManifestIncluded": False,
                "FullMaterializationDiagnosticsIncluded": False,
                "RawDomainValuesAndClaimsIncluded": False,
            },
        }


@dataclass(frozen=True)
class RawTrackAssignmentAttempt:
    """One member result inside the aggregate capacity proof."""

    TemplateId: str
    Objective: tuple[int, ...]
    Success: bool
    Complete: bool
    ExpansionCount: int
    CumulativeExpansionCount: int
    DiagnosticSelectedCandidateIds: tuple[tuple[str, str], ...] = ()
    ConflictSignals: tuple[str, ...] = ()
    ConflictResourceIndices: tuple[int, ...] = ()
    IncompleteReason: str = ""
    FailureNet: str = ""

    def __post_init__(self) -> None:
        if type(self.TemplateId) is not str or not self.TemplateId:
            raise ValueError("raw assignment attempt requires an id")
        _RequireExactIntegerTuple(self.Objective, "Attempt.Objective")
        _RequireExactBoolean(self.Success, "Attempt.Success")
        _RequireExactBoolean(self.Complete, "Attempt.Complete")
        _RequireExactNonBooleanInteger(
            self.ExpansionCount,
            "Attempt.ExpansionCount",
        )
        _RequireExactNonBooleanInteger(
            self.CumulativeExpansionCount,
            "Attempt.CumulativeExpansionCount",
        )
        _RequireExactStringPairTuple(
            self.DiagnosticSelectedCandidateIds,
            "Attempt.DiagnosticSelectedCandidateIds",
        )
        if type(self.ConflictSignals) is not tuple or any(
            type(Value) is not str for Value in self.ConflictSignals
        ):
            raise TypeError("attempt conflict signals must be exact strings")
        _RequireExactIntegerTuple(
            self.ConflictResourceIndices,
            "Attempt.ConflictResourceIndices",
        )
        if type(self.IncompleteReason) is not str:
            raise TypeError("attempt incomplete reason must be an exact string")
        if type(self.FailureNet) is not str:
            raise TypeError("attempt failure net must be an exact string")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "TemplateId": self.TemplateId,
            "Objective": list(self.Objective),
            "Success": self.Success,
            "Complete": self.Complete,
            "ExpansionCount": self.ExpansionCount,
            "CumulativeExpansionCount": self.CumulativeExpansionCount,
            "DiagnosticSelectedCandidateIds": [
                list(Value) for Value in self.DiagnosticSelectedCandidateIds
            ],
            "ConflictSignals": list(self.ConflictSignals),
            "ConflictResourceIndices": list(self.ConflictResourceIndices),
            "IncompleteReason": self.IncompleteReason,
            "FailureNet": self.FailureNet,
        }


class RawTrackAssignmentCandidatePreparationOutcome(str, Enum):
    """Complete-or-incomplete result for one exact portfolio candidate."""

    CompleteFeasible = "CompleteFeasible"
    CompleteFailed = "CompleteFailed"
    Incomplete = "Incomplete"


@dataclass(frozen=True)
class RawTrackAssignmentCandidatePreparationResult:
    """Immutable candidate preparation result consumed by pre-route choice."""

    CandidateId: str
    CandidateInputFingerprint: str
    CandidateInputManifest: RawTrackAssignmentCandidateInputManifest
    PortfolioFingerprint: str
    WorkControlsFingerprint: str
    Objective: tuple[int, ...]
    Outcome: RawTrackAssignmentCandidatePreparationOutcome
    Preparation: TrackAssignmentPreparation | None
    AvailableAssignmentExpansions: int
    ExpansionCount: int
    CumulativeExpansionCount: int
    DiagnosticSelectedCandidateIds: tuple[tuple[str, str], ...] = ()
    ConflictSignals: tuple[str, ...] = ()
    ConflictResourceIndices: tuple[int, ...] = ()
    IncompleteReason: str = ""
    FailureNet: str = ""
    _PreparationPayload: object = field(
        init=False,
        repr=False,
        compare=True,
        default=None,
    )

    def __post_init__(self) -> None:
        if type(self.CandidateId) is not str or not self.CandidateId:
            raise ValueError("candidate preparation result requires an id")
        if (
            type(self.CandidateInputFingerprint) is not str
            or not self.CandidateInputFingerprint
        ):
            raise ValueError(
                "candidate preparation result requires its input identity"
            )
        if (
            type(self.CandidateInputManifest)
            is not RawTrackAssignmentCandidateInputManifest
        ):
            raise TypeError(
                "candidate preparation result requires an exact input manifest"
            )
        if (
            self.CandidateInputFingerprint
            != self.CandidateInputManifest.ManifestFingerprint
        ):
            raise ValueError(
                "candidate preparation result input fingerprint mismatches "
                "its manifest"
            )
        if type(self.PortfolioFingerprint) is not str or not self.PortfolioFingerprint:
            raise ValueError(
                "candidate preparation result requires portfolio identity"
            )
        if (
            type(self.WorkControlsFingerprint) is not str
            or not self.WorkControlsFingerprint
        ):
            raise ValueError(
                "candidate preparation result requires work-control identity"
            )
        _RequireExactIntegerTuple(self.Objective, "CandidateResult.Objective")
        if type(self.Outcome) is not RawTrackAssignmentCandidatePreparationOutcome:
            raise TypeError("candidate preparation outcome must be typed")
        _RequireExactNonBooleanInteger(
            self.AvailableAssignmentExpansions,
            "CandidateResult.AvailableAssignmentExpansions",
        )
        _RequireExactNonBooleanInteger(
            self.ExpansionCount,
            "CandidateResult.ExpansionCount",
        )
        _RequireExactNonBooleanInteger(
            self.CumulativeExpansionCount,
            "CandidateResult.CumulativeExpansionCount",
        )
        if self.CumulativeExpansionCount < self.ExpansionCount:
            raise ValueError(
                "candidate preparation result has invalid work accounting"
            )
        if self.ExpansionCount > self.AvailableAssignmentExpansions:
            raise ValueError(
                "candidate preparation result exceeds its admitted work"
            )
        _RequireExactStringPairTuple(
            self.DiagnosticSelectedCandidateIds,
            "CandidateResult.DiagnosticSelectedCandidateIds",
        )
        if type(self.ConflictSignals) is not tuple or any(
            type(Value) is not str for Value in self.ConflictSignals
        ):
            raise TypeError(
                "candidate preparation conflict signals must be exact strings"
            )
        _RequireExactIntegerTuple(
            self.ConflictResourceIndices,
            "CandidateResult.ConflictResourceIndices",
        )
        if type(self.IncompleteReason) is not str:
            raise TypeError(
                "candidate preparation incomplete reason must be exact string"
            )
        if type(self.FailureNet) is not str:
            raise TypeError(
                "candidate preparation failure net must be exact string"
            )
        PreparationPayload = None
        if self.Preparation is not None:
            if type(self.Preparation) is not TrackAssignmentPreparation:
                raise TypeError(
                    "candidate preparation witness must be exact"
                )
            _RequireExactBoolean(
                self.Preparation.Success,
                "Preparation.Success",
            )
            _RequireExactBoolean(
                self.Preparation.Complete,
                "Preparation.Complete",
            )
            _RequireExactNonBooleanInteger(
                self.Preparation.ExpansionCount,
                "Preparation.ExpansionCount",
            )
            TuplePairs = (
                ("SelectedCandidateIds", self.Preparation.SelectedCandidateIds),
                ("CandidateCounts", self.Preparation.CandidateCounts),
                (
                    "SelectedLocalClaimChoiceIds",
                    self.Preparation.SelectedLocalClaimChoiceIds,
                ),
            )
            for Name, Values in TuplePairs:
                if type(Values) is not tuple or any(
                    type(Item) is not tuple
                    or len(Item) != 2
                    or type(Item[0]) is not str
                    or (
                        type(Item[1]) is not int
                        if Name == "CandidateCounts"
                        else type(Item[1]) is not str
                    )
                    for Item in Values
                ):
                    raise TypeError(f"Preparation.{Name} is not exact")
                if Name == "CandidateCounts":
                    for Index, (_Signal, Count) in enumerate(Values):
                        _RequireExactNonBooleanInteger(
                            Count,
                            f"Preparation.CandidateCounts[{Index}][1]",
                        )
            if type(self.Preparation.ConflictSignals) is not tuple or any(
                type(Value) is not str
                for Value in self.Preparation.ConflictSignals
            ):
                raise TypeError("Preparation.ConflictSignals is not exact")
            _RequireExactIntegerTuple(
                self.Preparation.ConflictResourceIndices,
                "Preparation.ConflictResourceIndices",
            )
            if type(self.Preparation.Diagnostics) is not tuple or any(
                type(Item) is not tuple
                or len(Item) != 2
                or type(Item[0]) is not str
                for Item in self.Preparation.Diagnostics
            ):
                raise TypeError("Preparation.Diagnostics is not exact")
            FrozenDiagnostics = tuple(
                (
                    Key,
                    _FreezePreparationDiagnosticValue(
                        Value,
                        Path=f"Preparation.Diagnostics.{Key}",
                    ),
                )
                for Key, Value in self.Preparation.Diagnostics
            )
            OwnedPreparation = replace(
                deepcopy(self.Preparation),
                Diagnostics=FrozenDiagnostics,
            )
            object.__setattr__(self, "Preparation", OwnedPreparation)
            PreparationPayload = _FreezeCandidateAuthorityValue(
                _TrackAssignmentPreparationAuthorityPayload(
                    OwnedPreparation
                ),
                Path="Preparation",
            )
        object.__setattr__(
            self,
            "_PreparationPayload",
            PreparationPayload,
        )
        if (
            self.Outcome
            is RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible
        ):
            if (
                self.Preparation is None
                or not self.Preparation.Success
                or not self.Preparation.Complete
                or self.IncompleteReason
            ):
                raise ValueError(
                    "complete feasible candidate requires one complete witness"
                )
            SelectedPreparationIds = tuple(sorted((
                *self.Preparation.SelectedCandidateIds,
                *self.Preparation.SelectedLocalClaimChoiceIds,
            )))
            if SelectedPreparationIds != tuple(sorted(
                self.DiagnosticSelectedCandidateIds
            )):
                raise ValueError(
                    "complete feasible candidate witness does not match its "
                    "validated native selection"
                )
        elif (
            self.Outcome
            is RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed
        ):
            if self.Preparation is not None or self.IncompleteReason:
                raise ValueError(
                    "complete failed candidate cannot carry a witness or "
                    "incomplete reason"
                )
        elif (
            self.Outcome
            is RawTrackAssignmentCandidatePreparationOutcome.Incomplete
        ):
            if self.Preparation is not None or not self.IncompleteReason:
                raise ValueError(
                    "incomplete candidate requires a reason and no witness"
                )
        else:
            raise ValueError("unknown candidate preparation outcome")

    @property
    def Complete(self) -> bool:
        return (
            self.Outcome
            is not RawTrackAssignmentCandidatePreparationOutcome.Incomplete
        )

    @property
    def Success(self) -> bool:
        return (
            self.Outcome
            is RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible
        )

    @property
    def ResultFingerprint(self) -> str:
        return BuildStableFingerprint({
            "Kind": "raw-track-assignment-candidate-preparation-result-v1",
            "CandidateId": self.CandidateId,
            "CandidateInputFingerprint": self.CandidateInputFingerprint,
            "CandidateInputManifest": (
                self.CandidateInputManifest.ToDictionary()
            ),
            "PortfolioFingerprint": self.PortfolioFingerprint,
            "WorkControlsFingerprint": self.WorkControlsFingerprint,
            "Objective": self.Objective,
            "Outcome": self.Outcome.value,
            "Preparation": (
                _SerializeFrozenCandidateAuthority(self._PreparationPayload)
                if self._PreparationPayload is not None
                else None
            ),
            "AvailableAssignmentExpansions": (
                self.AvailableAssignmentExpansions
            ),
            "ExpansionCount": self.ExpansionCount,
            "CumulativeExpansionCount": self.CumulativeExpansionCount,
            "DiagnosticSelectedCandidateIds": (
                self.DiagnosticSelectedCandidateIds
            ),
            "ConflictSignals": self.ConflictSignals,
            "ConflictResourceIndices": self.ConflictResourceIndices,
            "IncompleteReason": self.IncompleteReason,
            "FailureNet": self.FailureNet,
        })

    def ToDictionary(self) -> dict[str, object]:
        return {
            "ResultFingerprint": self.ResultFingerprint,
            "CandidateId": self.CandidateId,
            "CandidateInputFingerprint": self.CandidateInputFingerprint,
            "CandidateInputManifest": (
                self.CandidateInputManifest.ToDictionary()
            ),
            "PortfolioFingerprint": self.PortfolioFingerprint,
            "WorkControlsFingerprint": self.WorkControlsFingerprint,
            "Objective": list(self.Objective),
            "Outcome": self.Outcome.value,
            "Complete": self.Complete,
            "Success": self.Success,
            "Preparation": (
                _SerializeFrozenCandidateAuthority(self._PreparationPayload)
                if self._PreparationPayload is not None
                else None
            ),
            "AvailableAssignmentExpansions": (
                self.AvailableAssignmentExpansions
            ),
            "ExpansionCount": self.ExpansionCount,
            "CumulativeExpansionCount": self.CumulativeExpansionCount,
            "DiagnosticSelectedCandidateIds": [
                list(Value) for Value in self.DiagnosticSelectedCandidateIds
            ],
            "ConflictSignals": list(self.ConflictSignals),
            "ConflictResourceIndices": list(self.ConflictResourceIndices),
            "IncompleteReason": self.IncompleteReason,
            "FailureNet": self.FailureNet,
        }


@dataclass(frozen=True)
class RawTrackAssignmentSelection:
    """Typed terminal result for the one aggregate template selection."""

    ProblemFingerprint: str
    SelectionFingerprint: str
    SelectedTemplateId: str
    SelectedObjective: tuple[int, ...]
    Preparation: TrackAssignmentPreparation | None
    Attempts: tuple[RawTrackAssignmentAttempt, ...]
    ExpansionCount: int
    Success: bool
    Complete: bool
    Unsatisfiable: bool
    IncompleteReason: str = ""
    FirstConflictSignals: tuple[str, ...] = ()
    FirstConflictResourceIndices: tuple[int, ...] = ()
    MaterializedTemplateCount: int = 0
    SkippedDominatedTemplateCount: int = 0
    PortfolioTemplateCount: int = 0
    CandidatePreparationResults: tuple[
        RawTrackAssignmentCandidatePreparationResult, ...
    ] = ()
    OuterPortfolioComplete: bool = False

    def __post_init__(self) -> None:
        if type(self.ProblemFingerprint) is not str or not self.ProblemFingerprint:
            raise ValueError("selection requires a problem fingerprint")
        if type(self.SelectionFingerprint) is not str:
            raise TypeError("selection fingerprint must be an exact string")
        if type(self.SelectedTemplateId) is not str:
            raise TypeError("selected template id must be an exact string")
        _RequireExactIntegerTuple(
            self.SelectedObjective,
            "Selection.SelectedObjective",
        )
        if type(self.Attempts) is not tuple or any(
            type(Value) is not RawTrackAssignmentAttempt
            for Value in self.Attempts
        ):
            raise TypeError("selection attempts must be an exact tuple")
        _RequireExactNonBooleanInteger(
            self.ExpansionCount,
            "Selection.ExpansionCount",
        )
        _RequireExactBoolean(self.Success, "Selection.Success")
        _RequireExactBoolean(self.Complete, "Selection.Complete")
        _RequireExactBoolean(self.Unsatisfiable, "Selection.Unsatisfiable")
        _RequireExactBoolean(
            self.OuterPortfolioComplete,
            "Selection.OuterPortfolioComplete",
        )
        if type(self.IncompleteReason) is not str:
            raise TypeError("selection incomplete reason must be exact string")
        if type(self.FirstConflictSignals) is not tuple or any(
            type(Value) is not str for Value in self.FirstConflictSignals
        ):
            raise TypeError("selection conflict signals must be exact strings")
        _RequireExactIntegerTuple(
            self.FirstConflictResourceIndices,
            "Selection.FirstConflictResourceIndices",
        )
        _RequireExactNonBooleanInteger(
            self.MaterializedTemplateCount,
            "Selection.MaterializedTemplateCount",
        )
        _RequireExactNonBooleanInteger(
            self.SkippedDominatedTemplateCount,
            "Selection.SkippedDominatedTemplateCount",
        )
        _RequireExactNonBooleanInteger(
            self.PortfolioTemplateCount,
            "Selection.PortfolioTemplateCount",
        )
        if self.MaterializedTemplateCount > self.PortfolioTemplateCount:
            raise ValueError(
                "selection materialized template count exceeds its portfolio"
            )
        if type(self.CandidatePreparationResults) is not tuple or any(
            type(Value) is not RawTrackAssignmentCandidatePreparationResult
            for Value in self.CandidatePreparationResults
        ):
            raise TypeError(
                "selection candidate results must be an exact tuple"
            )
        CandidateIds = tuple(
            Result.CandidateId for Result in self.CandidatePreparationResults
        )
        if len(CandidateIds) != len(set(CandidateIds)):
            raise ValueError("selection repeats a candidate preparation result")
        if self.CandidatePreparationResults and any(
            Result.PortfolioFingerprint != self.ProblemFingerprint
            for Result in self.CandidatePreparationResults
        ):
            raise ValueError(
                "selection contains a result from another portfolio"
            )
        if len({
            Result.WorkControlsFingerprint
            for Result in self.CandidatePreparationResults
        }) > 1:
            raise ValueError(
                "selection mixes candidate preparation work controls"
            )
        if self.Complete and any(
            not Result.Complete
            for Result in self.CandidatePreparationResults
        ):
            raise ValueError(
                "complete selection contains an incomplete candidate result"
            )
        if self.Unsatisfiable and (
            self.Success
            or not self.Complete
            or not self.OuterPortfolioComplete
            or any(
                Result.Outcome
                is not RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed
                for Result in self.CandidatePreparationResults
            )
        ):
            raise ValueError(
                "unsatisfiable selection requires an exhaustive complete "
                "failed portfolio"
            )
        if self.Success and self.CandidatePreparationResults:
            Selected = tuple(
                Result
                for Result in self.CandidatePreparationResults
                if Result.CandidateId == self.SelectedTemplateId
            )
            if (
                len(Selected) != 1
                or not Selected[0].Success
                or Selected[0].Preparation != self.Preparation
            ):
                raise ValueError(
                    "selection witness is not its exact candidate result"
                )

    def RequireSelectedCandidatePreparation(
        self,
        *,
        CandidateId: str,
        CandidateInputFingerprint: str,
        CandidateInputManifest: RawTrackAssignmentCandidateInputManifest,
        WorkControlsFingerprint: str,
    ) -> RawTrackAssignmentCandidatePreparationResult:
        """Return only the exact candidate result selected by this portfolio."""
        Matches = tuple(
            Result
            for Result in self.CandidatePreparationResults
            if Result.CandidateId == CandidateId
        )
        if len(Matches) != 1:
            raise ValueError(
                "selected pre-route candidate has no exact preparation result"
            )
        Result = Matches[0]
        if CandidateId != self.SelectedTemplateId:
            raise ValueError(
                "requested preparation result is not the selected candidate"
            )
        if Result.CandidateInputFingerprint != CandidateInputFingerprint:
            raise ValueError(
                "selected candidate preparation input identity mismatches"
            )
        if Result.CandidateInputManifest != CandidateInputManifest:
            raise ValueError(
                "selected candidate preparation input manifest mismatches"
            )
        if Result.WorkControlsFingerprint != WorkControlsFingerprint:
            raise ValueError(
                "selected candidate preparation work controls mismatch"
            )
        if not Result.Success or Result.Preparation is None:
            raise ValueError(
                "selected candidate preparation is not complete feasible"
            )
        return Result

    def ToDictionary(self) -> dict[str, object]:
        SelectedCandidateResult = next((
            Result
            for Result in self.CandidatePreparationResults
            if Result.CandidateId == self.SelectedTemplateId
        ), None)
        return {
            "ProblemFingerprint": self.ProblemFingerprint,
            "SelectionFingerprint": self.SelectionFingerprint,
            "SelectedTemplateId": self.SelectedTemplateId,
            "SelectedObjective": list(self.SelectedObjective),
            "Preparation": (
                _SerializeFrozenCandidateAuthority(
                    SelectedCandidateResult._PreparationPayload
                )
                if (
                    SelectedCandidateResult is not None
                    and SelectedCandidateResult._PreparationPayload is not None
                )
                else self.Preparation.ToDictionary()
                if self.Preparation is not None
                else None
            ),
            "Attempts": [Value.ToDictionary() for Value in self.Attempts],
            "ExpansionCount": self.ExpansionCount,
            "Success": self.Success,
            "Complete": self.Complete,
            "Unsatisfiable": self.Unsatisfiable,
            "IncompleteReason": self.IncompleteReason,
            "FirstConflictSignals": list(self.FirstConflictSignals),
            "FirstConflictResourceIndices": list(
                self.FirstConflictResourceIndices
            ),
            "MaterializedTemplateCount": self.MaterializedTemplateCount,
            "SkippedDominatedTemplateCount": (
                self.SkippedDominatedTemplateCount
            ),
            "PortfolioTemplateCount": self.PortfolioTemplateCount,
            "CandidatePreparationResults": [
                Value.ToDictionary()
                for Value in self.CandidatePreparationResults
            ],
            "OuterPortfolioComplete": self.OuterPortfolioComplete,
        }

    def ToBoundedFailureEnvelope(self) -> dict[str, object]:
        """Publish failure semantics without recursive raw-input duplication."""
        SourceIdentities = [
            {
                "CandidateId": Result.CandidateId,
                "CandidateInputFingerprint": Result.CandidateInputFingerprint,
            }
            for Result in sorted(
                self.CandidatePreparationResults,
                key=lambda Result: Result.CandidateId,
            )
        ]
        UnattemptedTemplateCount = max(
            0,
            self.PortfolioTemplateCount - self.MaterializedTemplateCount,
        )
        return {
            "SchemaVersion": "raw-track-assignment-failure-envelope-v1",
            "SemanticResult": {
                "ProblemFingerprint": self.ProblemFingerprint,
                "SelectionFingerprint": self.SelectionFingerprint,
                "SelectedTemplateId": self.SelectedTemplateId,
                "SelectedObjective": list(self.SelectedObjective),
                "Success": self.Success,
                "Complete": self.Complete,
                "Unsatisfiable": self.Unsatisfiable,
                "IncompleteReason": self.IncompleteReason,
                "FirstConflictSignals": list(self.FirstConflictSignals),
                "FirstConflictResourceIndices": list(
                    self.FirstConflictResourceIndices
                ),
            },
            "SourceIdentities": SourceIdentities,
            "WorkIdentity": {
                "WorkControlsFingerprint": (
                    self.CandidatePreparationResults[0]
                    .WorkControlsFingerprint
                    if self.CandidatePreparationResults
                    else ""
                ),
                "ExpansionCount": self.ExpansionCount,
            },
            "Counts": {
                "PortfolioTemplateCount": self.PortfolioTemplateCount,
                "MaterializedTemplateCount": self.MaterializedTemplateCount,
                "AttemptCount": len(self.Attempts),
                "CandidatePreparationResultCount": len(
                    self.CandidatePreparationResults
                ),
                "SkippedDominatedTemplateCount": (
                    self.SkippedDominatedTemplateCount
                ),
                "UnattemptedTemplateCount": UnattemptedTemplateCount,
            },
            "EvidenceCompleteness": {
                "SemanticSelectionComplete": self.Complete,
                "OuterPortfolioComplete": self.OuterPortfolioComplete,
                "FullInputManifestIncluded": False,
                "FullMaterializationDiagnosticsIncluded": False,
                "UnattemptedDescriptorsDominated": (
                    UnattemptedTemplateCount > 0
                    and self.SkippedDominatedTemplateCount
                    == UnattemptedTemplateCount
                ),
            },
            "Omissions": [
                "full-frozen-candidate-input-manifests",
                "full-materialization-diagnostics",
                "raw-domain-values-and-claims",
            ],
        }


NativeRawAssignmentSolver = Callable[[RawTrackAssignmentDomain, int], Any]
WorkCheck = Callable[[dict[str, object]], None]
RawTrackAssignmentMaterializer = Callable[
    [RawTrackAssignmentPortfolioTemplate],
    RawTrackAssignmentMaterialization,
]


def _BuildRawTemplateMaterializationObservation(
    Portfolio: RawTrackAssignmentPortfolio,
    Descriptor: RawTrackAssignmentPortfolioTemplate,
    Materialization: RawTrackAssignmentMaterialization,
    *,
    MaterializedTemplateCount: int,
    AttemptCount: int,
    ExpansionCount: int,
) -> dict[str, object]:
    """Record a completed raw result before later deadline-sensitive work."""
    return {
        "Phase": "raw-template-materialization-observed",
        "SemanticResult": Materialization.ToBoundedFailureDictionary(),
        "TemplateId": Descriptor.TemplateId,
        "SourceIdentity": {
            "CandidateInputFingerprint": (
                Descriptor.MaterializationInputFingerprint
            ),
            "PortfolioFingerprint": Portfolio.ProblemFingerprint,
        },
        "WorkIdentity": {
            "WorkControlsFingerprint": Portfolio.WorkControlsFingerprint,
            "MaximumAssignmentExpansions": (
                Portfolio.MaximumAssignmentExpansions
            ),
        },
        "Counts": {
            "PortfolioTemplateCount": len(Portfolio.Templates),
            "MaterializedTemplateCount": MaterializedTemplateCount,
            "AttemptCount": AttemptCount,
            "ExpansionCount": ExpansionCount,
        },
        "EvidenceCompleteness": {
            "MaterializationReturned": True,
            "MaterializationComplete": Materialization.Complete,
            "FullInputManifestIncluded": False,
            "FullMaterializationDiagnosticsIncluded": False,
        },
        "Omissions": [
            "full-frozen-candidate-input-manifest",
            "full-materialization-diagnostics",
            "raw-domain-values-and-claims",
        ],
    }


def _NativeExactBoolean(Result: object, Name: str) -> bool:
    if not hasattr(Result, Name):
        raise TypeError(f"native assignment result omitted {Name}")
    return _RequireExactBoolean(
        getattr(Result, Name),
        f"NativeResult.{Name}",
    )


def _NativeExactInteger(Result: object, Name: str) -> int:
    if not hasattr(Result, Name):
        raise TypeError(f"native assignment result omitted {Name}")
    return _RequireExactNonBooleanInteger(
        getattr(Result, Name),
        f"NativeResult.{Name}",
    )


def _NativeExactStringTuple(Result: object, Name: str) -> tuple[str, ...]:
    Value = getattr(Result, Name, ())
    if type(Value) not in (tuple, list) or any(
        type(Item) is not str for Item in Value
    ):
        raise TypeError(f"NativeResult.{Name} must contain exact strings")
    return tuple(sorted(Value))


def _NativeExactIntegerTuple(Result: object, Name: str) -> tuple[int, ...]:
    Value = getattr(Result, Name, ())
    if type(Value) not in (tuple, list) or any(
        type(Item) is not int for Item in Value
    ):
        raise TypeError(
            f"NativeResult.{Name} must contain exact non-Boolean ints"
        )
    return tuple(sorted(Value))


def _NativeExactOptionalString(Result: object, Name: str) -> str:
    Value = getattr(Result, Name, "")
    if Value is None:
        return ""
    if type(Value) is not str:
        raise TypeError(f"NativeResult.{Name} must be an exact string")
    return Value


def _ValidateNativeSelectedCandidateIds(
    Result: object,
    Domain: RawTrackAssignmentDomain,
    *,
    Success: bool,
) -> tuple[tuple[str, str], ...]:
    """Require one exact allowed choice for every required success signal."""
    _RequireExactBoolean(Success, "NativeResult.Success")
    RawSelected = getattr(Result, "SelectedCandidateIds", None)
    if type(RawSelected) not in (tuple, list):
        raise TypeError(
            "NativeResult.SelectedCandidateIds must be an exact sequence"
        )
    Selected: list[tuple[str, str]] = []
    for Index, Pair in enumerate(RawSelected):
        if type(Pair) not in (tuple, list) or len(Pair) != 2:
            raise TypeError(
                f"NativeResult.SelectedCandidateIds[{Index}] must be a pair"
            )
        Signal, CandidateId = Pair
        if (
            type(Signal) is not str
            or not Signal
            or type(CandidateId) is not str
            or not CandidateId
        ):
            raise TypeError(
                "native selected candidate identities must be nonempty "
                "exact strings"
            )
        Selected.append((Signal, CandidateId))
    SelectedValues = tuple(Selected)
    if len(SelectedValues) != len(set(SelectedValues)):
        raise ValueError("native selection repeats a candidate pair")
    SelectedSignals = tuple(Signal for Signal, _CandidateId in SelectedValues)
    if len(SelectedSignals) != len(set(SelectedSignals)):
        raise ValueError("native selection contains multiple choices for a signal")

    if type(Domain.CandidateCounts) is not tuple:
        raise TypeError("raw domain candidate counts must be an exact tuple")
    RequiredSignals: list[str] = []
    DeclaredCounts: dict[str, int] = {}
    for Index, Pair in enumerate(Domain.CandidateCounts):
        if type(Pair) is not tuple or len(Pair) != 2:
            raise TypeError(
                f"Domain.CandidateCounts[{Index}] must be an exact pair"
            )
        Signal, Count = Pair
        if type(Signal) is not str or not Signal:
            raise TypeError("required signal identities must be exact strings")
        Count = _RequireExactNonBooleanInteger(
            Count,
            f"Domain.CandidateCounts[{Index}][1]",
        )
        if Signal in DeclaredCounts:
            raise ValueError("raw domain repeats a required signal")
        DeclaredCounts[Signal] = Count
        RequiredSignals.append(Signal)

    AllowedPairs: set[tuple[str, str]] = set()
    ActualCounts = {Signal: 0 for Signal in RequiredSignals}
    for Index, Value in enumerate(Domain.Values):
        if (
            type(Value.Signal) is not str
            or not Value.Signal
            or type(Value.CandidateId) is not str
            or not Value.CandidateId
        ):
            raise TypeError(
                f"Domain.Values[{Index}] has non-exact identities"
            )
        Pair = (Value.Signal, Value.CandidateId)
        if Pair in AllowedPairs:
            raise ValueError("raw domain repeats an allowed candidate pair")
        AllowedPairs.add(Pair)
        if Value.Signal not in ActualCounts:
            raise ValueError("raw domain contains an undeclared signal")
        ActualCounts[Value.Signal] += 1
    if ActualCounts != DeclaredCounts:
        raise ValueError("raw domain candidate counts do not match its values")

    Unknown = tuple(Pair for Pair in SelectedValues if Pair not in AllowedPairs)
    if Unknown:
        raise ValueError("native selection contains an unknown candidate")
    if not Success:
        # Native failure may retain an exact partial assignment for diagnosis.
        # It never enters a TrackAssignmentPreparation or gains authority.
        return SelectedValues
    if set(SelectedSignals) != set(RequiredSignals):
        raise ValueError(
            "successful native assignment does not cover every required signal"
        )
    return SelectedValues


def _BuildSelection(
    Problem: RawTrackAssignmentProblem | RawTrackAssignmentPortfolio,
    *,
    Attempts: Iterable[RawTrackAssignmentAttempt],
    ExpansionCount: int,
    Success: bool,
    Complete: bool,
    Unsatisfiable: bool,
    SelectedTemplate: RawTrackAssignmentTemplate | None = None,
    Preparation: TrackAssignmentPreparation | None = None,
    IncompleteReason: str = "",
    FirstConflictSignals: tuple[str, ...] = (),
    FirstConflictResourceIndices: tuple[int, ...] = (),
    MaterializedTemplateCount: int = 0,
    SkippedDominatedTemplateCount: int = 0,
    CandidatePreparationResults: Iterable[
        RawTrackAssignmentCandidatePreparationResult
    ] = (),
    SelectedCandidatePreparationResult: (
        RawTrackAssignmentCandidatePreparationResult | None
    ) = None,
) -> RawTrackAssignmentSelection:
    AttemptValues = tuple(Attempts)
    CandidateResultValues = tuple(CandidatePreparationResults)
    if SelectedCandidatePreparationResult is not None:
        if not SelectedCandidatePreparationResult.Success:
            raise ValueError(
                "selected candidate preparation result is not feasible"
            )
        if SelectedTemplate is None:
            raise ValueError(
                "selected candidate preparation result requires its template"
            )
        if (
            SelectedCandidatePreparationResult.CandidateId
            != SelectedTemplate.TemplateId
        ):
            raise ValueError(
                "selected candidate preparation result mismatches its template"
            )
        Preparation = SelectedCandidatePreparationResult.Preparation
    SelectionFingerprint = (
        BuildStableFingerprint({
            "ProblemFingerprint": Problem.ProblemFingerprint,
            "SelectedTemplateId": (
                SelectedTemplate.TemplateId
                if SelectedTemplate is not None
                else ""
            ),
            "Preparation": (
                Preparation.ToDictionary()
                if Preparation is not None
                else None
            ),
        })
        if Success and SelectedTemplate is not None and Preparation is not None
        else ""
    )
    return RawTrackAssignmentSelection(
        ProblemFingerprint=Problem.ProblemFingerprint,
        SelectionFingerprint=SelectionFingerprint,
        SelectedTemplateId=(
            SelectedTemplate.TemplateId
            if SelectedTemplate is not None
            else ""
        ),
        SelectedObjective=(
            SelectedTemplate.Objective
            if SelectedTemplate is not None
            else ()
        ),
        Preparation=Preparation,
        Attempts=AttemptValues,
        ExpansionCount=ExpansionCount,
        Success=Success,
        Complete=Complete,
        Unsatisfiable=Unsatisfiable,
        IncompleteReason=IncompleteReason,
        FirstConflictSignals=FirstConflictSignals,
        FirstConflictResourceIndices=FirstConflictResourceIndices,
        MaterializedTemplateCount=MaterializedTemplateCount,
        SkippedDominatedTemplateCount=SkippedDominatedTemplateCount,
        PortfolioTemplateCount=len(Problem.Templates),
        CandidatePreparationResults=CandidateResultValues,
        OuterPortfolioComplete=(
            not Problem.NonExhaustiveTemplateDomain
        ),
    )


def _EmptyDomainAttempt(
    Template: RawTrackAssignmentTemplate,
    ExpansionCount: int,
) -> RawTrackAssignmentAttempt | None:
    """Return an exact complete empty-domain core, if one is declared."""
    EmptySignals = tuple(
        Signal
        for Signal, Count in Template.Domain.CandidateCounts
        if Count == 0
    )
    if not EmptySignals:
        return None
    return RawTrackAssignmentAttempt(
        TemplateId=Template.TemplateId,
        Objective=Template.Objective,
        Success=False,
        Complete=True,
        ExpansionCount=0,
        CumulativeExpansionCount=ExpansionCount,
        ConflictSignals=EmptySignals,
        IncompleteReason="complete-empty-candidate-domain",
    )


def SolveRawTrackAssignmentProblem(
    Problem: RawTrackAssignmentProblem,
    NativeSolve: NativeRawAssignmentSolver,
    *,
    WorkCheck: WorkCheck | None = None,
) -> RawTrackAssignmentSelection:
    """Select one template and its authoritative witness under one cap.

    ``NativeSolve`` must run the raw domain's exact capacity-one assignment
    using at most the passed *remaining global* expansion count.  A complete
    failed member is a capacity core for that fixed member and permits the
    next, already-materialized member.  Any incomplete member terminates the
    entire non-retrying selection immediately.
    """
    OrderedTemplates = tuple(sorted(
        Problem.Templates,
        key=lambda Value: (Value.Objective, Value.TemplateId),
    ))
    Attempts: list[RawTrackAssignmentAttempt] = []
    Spent = 0
    FirstConflictSignals: tuple[str, ...] = ()
    FirstConflictResourceIndices: tuple[int, ...] = ()

    for TemplateIndex, Template in enumerate(OrderedTemplates):
        if WorkCheck is not None:
            WorkCheck({
                "Phase": "raw-template-track-assignment",
                "TemplateIndex": TemplateIndex,
                "TemplateCount": len(OrderedTemplates),
                "TemplateId": Template.TemplateId,
                "ExpansionCount": Spent,
                "MaximumAssignmentExpansions": (
                    Problem.MaximumAssignmentExpansions
                ),
            })
        if not Template.Domain.Complete:
            Attempts.append(RawTrackAssignmentAttempt(
                TemplateId=Template.TemplateId,
                Objective=Template.Objective,
                Success=False,
                Complete=False,
                ExpansionCount=0,
                CumulativeExpansionCount=Spent,
                IncompleteReason=(
                    Template.Domain.IncompleteReason
                    or "incomplete-raw-template-domain"
                ),
            ))
            return _BuildSelection(
                Problem,
                Attempts=Attempts,
                ExpansionCount=Spent,
                Success=False,
                Complete=False,
                Unsatisfiable=False,
                IncompleteReason="incomplete-template-domain",
                FirstConflictSignals=FirstConflictSignals,
                FirstConflictResourceIndices=FirstConflictResourceIndices,
            )

        EmptyDomain = _EmptyDomainAttempt(Template, Spent)
        if EmptyDomain is not None:
            Attempts.append(EmptyDomain)
            if not FirstConflictSignals:
                FirstConflictSignals = EmptyDomain.ConflictSignals
            continue

        Remaining = Problem.MaximumAssignmentExpansions - Spent
        if Remaining < 1:
            return _BuildSelection(
                Problem,
                Attempts=Attempts,
                ExpansionCount=Spent,
                Success=False,
                Complete=False,
                Unsatisfiable=False,
                IncompleteReason="assignment-work-cap",
                FirstConflictSignals=FirstConflictSignals,
                FirstConflictResourceIndices=FirstConflictResourceIndices,
            )
        NativeResult = NativeSolve(Template.Domain, Remaining)
        ResultExpansionCount = _NativeExactInteger(
            NativeResult,
            "ExpansionCount",
        )
        DeadlineExceeded = _NativeExactBoolean(
            NativeResult,
            "DeadlineExceeded",
        )
        BudgetExhausted = _NativeExactBoolean(
            NativeResult,
            "BudgetExhausted",
        )
        ResultSuccess = _NativeExactBoolean(NativeResult, "Success")
        ConflictSignals = _NativeExactStringTuple(
            NativeResult,
            "ConflictSignals",
        )
        ConflictResourceIndices = _NativeExactIntegerTuple(
            NativeResult,
            "ConflictResourceIndices",
        )
        DiagnosticSelectedCandidateIds = _ValidateNativeSelectedCandidateIds(
            NativeResult,
            Template.Domain,
            Success=ResultSuccess,
        )
        Spent = min(
            Problem.MaximumAssignmentExpansions,
            Spent + ResultExpansionCount,
        )
        ResultComplete = not DeadlineExceeded and not BudgetExhausted
        IncompleteReason = (
            "assignment-deadline"
            if DeadlineExceeded
            else "assignment-work-cap"
            if BudgetExhausted
            else ""
        )
        Attempt = RawTrackAssignmentAttempt(
            TemplateId=Template.TemplateId,
            Objective=Template.Objective,
            Success=ResultSuccess and ResultComplete,
            Complete=ResultComplete,
            ExpansionCount=ResultExpansionCount,
            CumulativeExpansionCount=Spent,
            DiagnosticSelectedCandidateIds=(
                DiagnosticSelectedCandidateIds
            ),
            ConflictSignals=ConflictSignals,
            ConflictResourceIndices=ConflictResourceIndices,
            IncompleteReason=IncompleteReason,
            FailureNet=_NativeExactOptionalString(
                NativeResult,
                "FailureNet",
            ),
        )
        Attempts.append(Attempt)
        if not FirstConflictSignals and ConflictSignals:
            FirstConflictSignals = ConflictSignals
            FirstConflictResourceIndices = ConflictResourceIndices
        if not ResultComplete:
            return _BuildSelection(
                Problem,
                Attempts=Attempts,
                ExpansionCount=Spent,
                Success=False,
                Complete=False,
                Unsatisfiable=False,
                IncompleteReason=IncompleteReason,
                FirstConflictSignals=FirstConflictSignals,
                FirstConflictResourceIndices=FirstConflictResourceIndices,
            )
        if ResultSuccess:
            Preparation = BuildTrackAssignmentPreparationFromRawDomain(
                Template.Domain,
                NativeResult,
            )
            if not Preparation.Success or not Preparation.Complete:
                raise RuntimeError(
                    "complete native raw assignment did not produce a "
                    "complete frozen track witness"
                )
            return _BuildSelection(
                Problem,
                Attempts=Attempts,
                ExpansionCount=Spent,
                Success=True,
                Complete=True,
                Unsatisfiable=False,
                SelectedTemplate=Template,
                Preparation=Preparation,
                FirstConflictSignals=FirstConflictSignals,
                FirstConflictResourceIndices=FirstConflictResourceIndices,
            )

    Complete = True
    Unsatisfiable = not Problem.NonExhaustiveTemplateDomain
    return _BuildSelection(
        Problem,
        Attempts=Attempts,
        ExpansionCount=Spent,
        Success=False,
        Complete=Complete,
        Unsatisfiable=Unsatisfiable,
        IncompleteReason=(
            "complete-capacity-core"
            if Unsatisfiable
            else "non-exhaustive-template-domain"
        ),
        FirstConflictSignals=FirstConflictSignals,
        FirstConflictResourceIndices=FirstConflictResourceIndices,
    )


def SolveRawTrackAssignmentPortfolio(
    Portfolio: RawTrackAssignmentPortfolio,
    Materialize: RawTrackAssignmentMaterializer,
    NativeSolve: NativeRawAssignmentSolver,
    *,
    WorkCheck: WorkCheck | None = None,
) -> RawTrackAssignmentSelection:
    """Select from fixed descriptors without eagerly building worse domains.

    A descriptor's selection prefix is immutable before raw-domain
    construction.  Consequently, once a prefix group has a witness, every
    descriptor after that group is strictly worse and is intentionally never
    materialized.  All equal-prefix descriptors are still materialized before
    committing the group: an incomplete tied member must remain a terminal
    incomplete result rather than being hidden by an earlier tie.  A typed
    materialization may append material/access tie-break terms, but may never
    change its declared prefix.  Materializing a member is pre-route
    candidate construction, not a routing retry.
    """
    OrderedDescriptors = tuple(sorted(
        Portfolio.Templates,
        key=lambda Value: (Value.Objective, Value.TemplateId),
    ))
    Attempts: list[RawTrackAssignmentAttempt] = []
    CandidateResults: list[
        RawTrackAssignmentCandidatePreparationResult
    ] = []
    Spent = 0
    FirstConflictSignals: tuple[str, ...] = ()
    FirstConflictResourceIndices: tuple[int, ...] = ()

    def CandidateResult(
        Descriptor: RawTrackAssignmentPortfolioTemplate,
        *,
        Objective: tuple[int, ...],
        Outcome: RawTrackAssignmentCandidatePreparationOutcome,
        AvailableAssignmentExpansions: int,
        ExpansionCount: int = 0,
        Preparation: TrackAssignmentPreparation | None = None,
        ConflictSignals: tuple[str, ...] = (),
        ConflictResourceIndices: tuple[int, ...] = (),
        DiagnosticSelectedCandidateIds: tuple[tuple[str, str], ...] = (),
        IncompleteReason: str = "",
        FailureNet: str = "",
    ) -> RawTrackAssignmentCandidatePreparationResult:
        return RawTrackAssignmentCandidatePreparationResult(
            CandidateId=Descriptor.TemplateId,
            CandidateInputFingerprint=(
                Descriptor.MaterializationInputFingerprint
            ),
            CandidateInputManifest=(
                Descriptor.MaterializationInputManifest
            ),
            PortfolioFingerprint=Portfolio.ProblemFingerprint,
            WorkControlsFingerprint=Portfolio.WorkControlsFingerprint,
            Objective=Objective,
            Outcome=Outcome,
            Preparation=Preparation,
            AvailableAssignmentExpansions=(
                AvailableAssignmentExpansions
            ),
            ExpansionCount=ExpansionCount,
            CumulativeExpansionCount=Spent,
            DiagnosticSelectedCandidateIds=(
                DiagnosticSelectedCandidateIds
            ),
            ConflictSignals=ConflictSignals,
            ConflictResourceIndices=ConflictResourceIndices,
            IncompleteReason=IncompleteReason,
            FailureNet=FailureNet,
        )

    TemplateIndex = 0
    while TemplateIndex < len(OrderedDescriptors):
        Objective = OrderedDescriptors[TemplateIndex].Objective
        SuccessfulMembers: list[
            tuple[
                RawTrackAssignmentTemplate,
                RawTrackAssignmentCandidatePreparationResult,
            ]
        ] = []
        while (
            TemplateIndex < len(OrderedDescriptors)
            and OrderedDescriptors[TemplateIndex].Objective == Objective
        ):
            Descriptor = OrderedDescriptors[TemplateIndex]
            Available = max(
                0,
                Portfolio.MaximumAssignmentExpansions - Spent,
            )
            if WorkCheck is not None:
                WorkCheck({
                    "Phase": "raw-template-domain-materialization",
                    "TemplateIndex": TemplateIndex,
                    "TemplateCount": len(OrderedDescriptors),
                    "TemplateId": Descriptor.TemplateId,
                    "CandidateInputFingerprint": (
                        Descriptor.MaterializationInputFingerprint
                    ),
                    "WorkControlsFingerprint": (
                        Portfolio.WorkControlsFingerprint
                    ),
                    "ExpansionCount": Spent,
                    "MaximumAssignmentExpansions": (
                        Portfolio.MaximumAssignmentExpansions
                    ),
                })
            Materialization = Materialize(Descriptor)
            if WorkCheck is not None:
                WorkCheck(_BuildRawTemplateMaterializationObservation(
                    Portfolio,
                    Descriptor,
                    Materialization,
                    MaterializedTemplateCount=TemplateIndex + 1,
                    AttemptCount=len(Attempts),
                    ExpansionCount=Spent,
                ))
            if Materialization.TemplateId != Descriptor.TemplateId:
                raise ValueError(
                    "raw template materializer returned a mismatched "
                    "template id"
                )
            if (
                Materialization.MaterializationInputFingerprint
                != Descriptor.MaterializationInputFingerprint
            ):
                raise ValueError(
                    "raw template materializer returned a mismatched "
                    "input fingerprint"
                )
            if (
                Materialization.MaterializationInputManifest
                != Descriptor.MaterializationInputManifest
            ):
                raise ValueError(
                    "raw template materializer returned a mismatched "
                    "input manifest"
                )
            if (
                not Materialization.Complete
                or Materialization.Domain is None
            ):
                Attempt = RawTrackAssignmentAttempt(
                    TemplateId=Descriptor.TemplateId,
                    Objective=Descriptor.Objective,
                    Success=False,
                    Complete=False,
                    ExpansionCount=0,
                    CumulativeExpansionCount=Spent,
                    IncompleteReason=Materialization.IncompleteReason,
                )
                Attempts.append(Attempt)
                CandidateResults.append(CandidateResult(
                    Descriptor,
                    Objective=Descriptor.Objective,
                    Outcome=(
                        RawTrackAssignmentCandidatePreparationOutcome.Incomplete
                    ),
                    AvailableAssignmentExpansions=Available,
                    IncompleteReason=Materialization.IncompleteReason,
                ))
                return _BuildSelection(
                    Portfolio,
                    Attempts=Attempts,
                    ExpansionCount=Spent,
                    Success=False,
                    Complete=False,
                    Unsatisfiable=False,
                    IncompleteReason="incomplete-template-domain",
                    FirstConflictSignals=FirstConflictSignals,
                    FirstConflictResourceIndices=(
                        FirstConflictResourceIndices
                    ),
                    MaterializedTemplateCount=TemplateIndex + 1,
                    SkippedDominatedTemplateCount=0,
                    CandidatePreparationResults=CandidateResults,
                )
            ResolvedObjective = (
                Materialization.ResolvedObjective
                or Descriptor.Objective
            )
            if (
                ResolvedObjective[:len(Descriptor.Objective)]
                != Descriptor.Objective
            ):
                raise ValueError(
                    "raw template resolved objective must retain its "
                    "declared selection prefix: "
                    + Descriptor.TemplateId
                )
            Domain = Materialization.Domain
            if (
                Domain.MaximumAssignmentExpansions
                != Portfolio.MaximumAssignmentExpansions
            ):
                raise ValueError(
                    "raw portfolio members must share one work cap: "
                    + Descriptor.TemplateId
                )
            if (
                not Portfolio.NonExhaustiveTemplateDomain
                and bool(dict(Domain.Diagnostics).get(
                    "ExcludedConfiguredRequestCounts",
                    (),
                ))
            ):
                raise ValueError(
                    "a raw portfolio member with excluded configured "
                    "request shapes cannot be declared exhaustive: "
                    + Descriptor.TemplateId
                )
            Template = RawTrackAssignmentTemplate(
                TemplateId=Descriptor.TemplateId,
                Objective=ResolvedObjective,
                Domain=Domain,
            )
            EmptyDomain = _EmptyDomainAttempt(Template, Spent)
            if EmptyDomain is not None:
                Attempts.append(EmptyDomain)
                CandidateResults.append(CandidateResult(
                    Descriptor,
                    Objective=ResolvedObjective,
                    Outcome=(
                        RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed
                    ),
                    AvailableAssignmentExpansions=Available,
                    ConflictSignals=EmptyDomain.ConflictSignals,
                ))
                if not FirstConflictSignals:
                    FirstConflictSignals = EmptyDomain.ConflictSignals
                TemplateIndex += 1
                continue

            Remaining = Portfolio.MaximumAssignmentExpansions - Spent
            if Remaining < 1:
                CandidateResults.append(CandidateResult(
                    Descriptor,
                    Objective=ResolvedObjective,
                    Outcome=(
                        RawTrackAssignmentCandidatePreparationOutcome.Incomplete
                    ),
                    AvailableAssignmentExpansions=0,
                    IncompleteReason="assignment-work-cap",
                ))
                return _BuildSelection(
                    Portfolio,
                    Attempts=Attempts,
                    ExpansionCount=Spent,
                    Success=False,
                    Complete=False,
                    Unsatisfiable=False,
                    IncompleteReason="assignment-work-cap",
                    FirstConflictSignals=FirstConflictSignals,
                    FirstConflictResourceIndices=(
                        FirstConflictResourceIndices
                    ),
                    MaterializedTemplateCount=TemplateIndex + 1,
                    SkippedDominatedTemplateCount=0,
                    CandidatePreparationResults=CandidateResults,
                )
            NativeResult = NativeSolve(Domain, Remaining)
            ResultExpansionCount = _NativeExactInteger(
                NativeResult,
                "ExpansionCount",
            )
            DeadlineExceeded = _NativeExactBoolean(
                NativeResult,
                "DeadlineExceeded",
            )
            BudgetExhausted = _NativeExactBoolean(
                NativeResult,
                "BudgetExhausted",
            )
            ResultSuccess = _NativeExactBoolean(NativeResult, "Success")
            ConflictSignals = _NativeExactStringTuple(
                NativeResult,
                "ConflictSignals",
            )
            ConflictResourceIndices = _NativeExactIntegerTuple(
                NativeResult,
                "ConflictResourceIndices",
            )
            DiagnosticSelectedCandidateIds = _ValidateNativeSelectedCandidateIds(
                NativeResult,
                Domain,
                Success=ResultSuccess,
            )
            Spent = min(
                Portfolio.MaximumAssignmentExpansions,
                Spent + ResultExpansionCount,
            )
            ResultComplete = not DeadlineExceeded and not BudgetExhausted
            IncompleteReason = (
                "assignment-deadline"
                if DeadlineExceeded
                else "assignment-work-cap"
                if BudgetExhausted
                else ""
            )
            FailureNet = _NativeExactOptionalString(
                NativeResult,
                "FailureNet",
            )
            Attempts.append(RawTrackAssignmentAttempt(
                TemplateId=Template.TemplateId,
                Objective=Template.Objective,
                Success=ResultSuccess and ResultComplete,
                Complete=ResultComplete,
                ExpansionCount=ResultExpansionCount,
                CumulativeExpansionCount=Spent,
                DiagnosticSelectedCandidateIds=(
                    DiagnosticSelectedCandidateIds
                ),
                ConflictSignals=ConflictSignals,
                ConflictResourceIndices=ConflictResourceIndices,
                IncompleteReason=IncompleteReason,
                FailureNet=FailureNet,
            ))
            if not FirstConflictSignals and ConflictSignals:
                FirstConflictSignals = ConflictSignals
                FirstConflictResourceIndices = ConflictResourceIndices
            if not ResultComplete:
                CandidateResults.append(CandidateResult(
                    Descriptor,
                    Objective=ResolvedObjective,
                    Outcome=(
                        RawTrackAssignmentCandidatePreparationOutcome.Incomplete
                    ),
                    AvailableAssignmentExpansions=Remaining,
                    ExpansionCount=ResultExpansionCount,
                    DiagnosticSelectedCandidateIds=(
                        DiagnosticSelectedCandidateIds
                    ),
                    ConflictSignals=ConflictSignals,
                    ConflictResourceIndices=ConflictResourceIndices,
                    IncompleteReason=IncompleteReason,
                    FailureNet=FailureNet,
                ))
                return _BuildSelection(
                    Portfolio,
                    Attempts=Attempts,
                    ExpansionCount=Spent,
                    Success=False,
                    Complete=False,
                    Unsatisfiable=False,
                    IncompleteReason=IncompleteReason,
                    FirstConflictSignals=FirstConflictSignals,
                    FirstConflictResourceIndices=(
                        FirstConflictResourceIndices
                    ),
                    MaterializedTemplateCount=TemplateIndex + 1,
                    SkippedDominatedTemplateCount=0,
                    CandidatePreparationResults=CandidateResults,
                )
            Preparation = None
            Outcome = (
                RawTrackAssignmentCandidatePreparationOutcome.CompleteFailed
            )
            if ResultSuccess:
                Preparation = BuildTrackAssignmentPreparationFromRawDomain(
                    Template.Domain,
                    NativeResult,
                )
                if not Preparation.Success or not Preparation.Complete:
                    raise RuntimeError(
                        "complete native raw assignment did not produce a "
                        "complete frozen track witness"
                    )
                Outcome = (
                    RawTrackAssignmentCandidatePreparationOutcome.CompleteFeasible
                )
            Result = CandidateResult(
                Descriptor,
                Objective=ResolvedObjective,
                Outcome=Outcome,
                AvailableAssignmentExpansions=Remaining,
                ExpansionCount=ResultExpansionCount,
                Preparation=Preparation,
                DiagnosticSelectedCandidateIds=(
                    DiagnosticSelectedCandidateIds
                ),
                ConflictSignals=ConflictSignals,
                ConflictResourceIndices=ConflictResourceIndices,
                FailureNet=FailureNet,
            )
            CandidateResults.append(Result)
            if Result.Success:
                SuccessfulMembers.append((Template, Result))
            TemplateIndex += 1

        if SuccessfulMembers:
            Winner, WinnerResult = min(
                SuccessfulMembers,
                key=lambda Value: (
                    Value[0].Objective,
                    Value[0].TemplateId,
                ),
            )
            return _BuildSelection(
                Portfolio,
                Attempts=Attempts,
                ExpansionCount=Spent,
                Success=True,
                Complete=True,
                Unsatisfiable=False,
                SelectedTemplate=Winner,
                SelectedCandidatePreparationResult=WinnerResult,
                FirstConflictSignals=FirstConflictSignals,
                FirstConflictResourceIndices=FirstConflictResourceIndices,
                MaterializedTemplateCount=TemplateIndex,
                SkippedDominatedTemplateCount=(
                    len(OrderedDescriptors) - TemplateIndex
                ),
                CandidatePreparationResults=CandidateResults,
            )

    Unsatisfiable = not Portfolio.NonExhaustiveTemplateDomain
    return _BuildSelection(
        Portfolio,
        Attempts=Attempts,
        ExpansionCount=Spent,
        Success=False,
        Complete=True,
        Unsatisfiable=Unsatisfiable,
        IncompleteReason=(
            "complete-capacity-core"
            if Unsatisfiable
            else "non-exhaustive-template-domain"
        ),
        FirstConflictSignals=FirstConflictSignals,
        FirstConflictResourceIndices=FirstConflictResourceIndices,
        MaterializedTemplateCount=len(OrderedDescriptors),
        CandidatePreparationResults=CandidateResults,
    )


def _BuildContextNativeRawAssignmentSolver(
    Context: Any | None,
    Deadline: RoutingDeadline,
) -> NativeRawAssignmentSolver:
    """Bind the existing native assignment API to one absolute deadline."""
    def NativeSolve(
        Domain: RawTrackAssignmentDomain,
        MaximumExpansions: int,
    ) -> Any:
        ActiveContext = (
            Domain.NativeAssignmentContext
            if Domain.NativeAssignmentContext is not None
            else Context
        )
        if ActiveContext is None:
            raise ValueError(
                "raw template assignment requires a native routing context"
            )
        RemainingMilliseconds = Deadline.RemainingMilliseconds()
        if RemainingMilliseconds < 1:
            return SimpleNamespace(
                Success=False,
                SelectedCandidateIds=(),
                ExpansionCount=0,
                BudgetExhausted=False,
                DeadlineExceeded=True,
                ConflictSignals=(),
                ConflictResourceIndices=(),
            )
        CandidateValues = Domain.NativeCandidateValues()
        BaseValues = Domain.NativeBaseValues()
        Arguments = (
            CandidateValues,
            len(Domain.ResourcePositions),
            MaximumExpansions,
            RemainingMilliseconds,
        )
        if BaseValues:
            return ActiveContext.PlanAuthoritativeRoutesWithBaseBounded(
                CandidateValues,
                BaseValues,
                len(Domain.ResourcePositions),
                MaximumExpansions,
                RemainingMilliseconds,
            )
        return ActiveContext.PlanAuthoritativeRoutesBounded(*Arguments)

    return NativeSolve


def SolveRawTrackAssignmentProblemWithContext(
    Problem: RawTrackAssignmentProblem,
    *,
    Context: Any | None = None,
    Deadline: RoutingDeadline,
    WorkCheck: WorkCheck | None = None,
) -> RawTrackAssignmentSelection:
    """Run the aggregate selector through the existing Rust binding.

    The ordinary bounded assignment API is deliberately reused.  A raw domain
    may retain the context that created its local resource index; ``Context``
    is a fallback for synthetic or fixture domains.  Each call receives only
    the global remainder and the same absolute deadline's remaining
    milliseconds, so the outer selector has one work cap and one deadline
    even though template resource indices are local.
    """
    return SolveRawTrackAssignmentProblem(
        Problem,
        _BuildContextNativeRawAssignmentSolver(Context, Deadline),
        WorkCheck=WorkCheck,
    )


def SolveRawTrackAssignmentPortfolioWithContext(
    Portfolio: RawTrackAssignmentPortfolio,
    Materialize: RawTrackAssignmentMaterializer,
    *,
    Context: Any | None = None,
    Deadline: RoutingDeadline,
    WorkCheck: WorkCheck | None = None,
) -> RawTrackAssignmentSelection:
    """Run one lazy fixed portfolio through the existing native binding."""
    ExpectedWorkControlsFingerprint = (
        BuildRawTrackAssignmentWorkControlsFingerprint(
            Portfolio.MaximumAssignmentExpansions,
            Deadline,
        )
    )
    if Portfolio.WorkControlsFingerprint != ExpectedWorkControlsFingerprint:
        raise ValueError(
            "raw template portfolio work controls do not match the "
            "caller-owned deadline"
        )
    return SolveRawTrackAssignmentPortfolio(
        Portfolio,
        Materialize,
        _BuildContextNativeRawAssignmentSolver(Context, Deadline),
        WorkCheck=WorkCheck,
    )
