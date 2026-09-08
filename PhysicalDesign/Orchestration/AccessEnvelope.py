"""Fresh, typed authority for current selected-access orchestration gates.

The envelope records complete owned inputs for one causal phase.  It is not a
serializer-derived source of authority: every usable result is produced by a
fresh Physical validation against the live placement, resource graph,
technology, and frozen wires supplied to that gate.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from math import isfinite
from typing import Any, Mapping

from PhysicalDesign.Contracts.Failures import (
    RoutingFailure,
    RoutingFailureReason,
    RoutingStageError,
)
from PhysicalDesign.Contracts.Placement import (
    ClusterInterfacePlacementState,
    InterClusterRoutingChannel,
    TrackAssignmentPreparation,
)
from PhysicalDesign.Contracts.PlacementAccess import (
    CurrentSelectedPlacementAccessValidation,
    CurrentSelectedPlacementAccessValidationReason,
    CurrentSelectedPlacementAccessValidationStatus,
    PlacementAccessSolveResult,
    PlacementAccessSolveStatus,
    PlacedPinAccessOptionDomain,
    SelectedPlacementPinAccessWitness,
)
from PhysicalDesign.Geometry.Placement import PlacedGate
from PhysicalDesign.Placement.Access.Validation import (
    ValidateCurrentSelectedPlacementAccess,
)
from PhysicalDesign.Placement.Engine.Clusters import (
    BuildPhysicalClusterBoundaryLeaseRequests,
    PcbPlacement,
    TranslateClusterLocalRouteClaim,
)
from PhysicalDesign.Placement.PreRouteInterface import DerivedRoutingEnvelope
from PhysicalDesign.Policy import PhysicalDesignPolicy
from PhysicalDesign.Redstone.Technology import RedstoneRoutingTechnology
from PhysicalDesign.Resources.ResourceGraph import (
    RoutingResourceGraph,
    RoutingResourceGraphVersion,
)
from PhysicalDesign.Routing.Assignment.TemplateAssignment import (
    RawTrackAssignmentAttempt,
    RawTrackAssignmentSelection,
)
from PhysicalDesign.Runtime.Reliability import BuildStableFingerprint


class CurrentSelectedAccessEnvelopeStatus(str, Enum):
    """Closed classification for one fresh Joint orchestration gate."""

    Ready = "Ready"
    Stale = "Stale"
    Incomplete = "Incomplete"
    Unsatisfiable = "Unsatisfiable"
    Unavailable = "Unavailable"


class CurrentSelectedAccessEnvelopePhase(str, Enum):
    """Causal phase whose current inputs were freshly re-attested."""

    BeforeRawMaterialization = "BeforeRawMaterialization"
    SelectedTrackSuccessor = "SelectedTrackSuccessor"
    BeforePublication = "BeforePublication"


class CurrentSelectedAccessTransition(str, Enum):
    """Actual transition which produced the phase-local placement."""

    InitialCandidate = "InitialCandidate"
    SelectedTrackAssignment = "SelectedTrackAssignment"
    ChannelReplacement = "ChannelReplacement"
    PostRoutingCompaction = "PostRoutingCompaction"


class CurrentSelectedAccessEnvelopeReason(str, Enum):
    """Stable result reasons without turning diagnostics into authority."""

    Current = "Current"
    PhysicalInputMismatch = "PhysicalInputMismatch"
    PhysicalInputUnresolved = "PhysicalInputUnresolved"
    CurrentInputChangedDuringCapture = "CurrentInputChangedDuringCapture"
    UnexpectedPhaseDrift = "UnexpectedPhaseDrift"
    AccessPolicyDisabled = "AccessPolicyDisabled"
    MissingSelectedAccessEvidence = "MissingSelectedAccessEvidence"
    UnsupportedResourceGraphVersion = "UnsupportedResourceGraphVersion"
    PlacementFingerprintModeUnproven = "PlacementFingerprintModeUnproven"
    MissingRoutingEnvelope = "MissingRoutingEnvelope"
    MissingTrackPreparation = "MissingTrackPreparation"
    IncompleteTrackPreparation = "IncompleteTrackPreparation"
    MissingRawTrackAssignment = "MissingRawTrackAssignment"
    IncompleteRawTrackAssignment = "IncompleteRawTrackAssignment"
    MissingReadyPredecessor = "MissingReadyPredecessor"
    AccessSolveIncomplete = "AccessSolveIncomplete"
    AccessSolveUnsatisfiable = "AccessSolveUnsatisfiable"
    SolvePolicyMismatch = "SolvePolicyMismatch"
    TrackPreparationMismatch = "TrackPreparationMismatch"
    RawTrackAssignmentMismatch = "RawTrackAssignmentMismatch"
    PlacementTransitionMismatch = "PlacementTransitionMismatch"


def _FreezeAuthorityValue(
    Value: object,
    *,
    Path: str,
    Active: set[int] | None = None,
) -> object:
    """Detach a complete declared input into a deterministic immutable tree."""
    if Value is None or type(Value) in (bool, int, str):
        return Value
    if type(Value) is float:
        if not isfinite(Value):
            raise TypeError(f"{Path} contains a non-finite value")
        return Value
    if isinstance(Value, Enum):
        return _FreezeAuthorityValue(Value.value, Path=Path, Active=Active)
    if Active is None:
        Active = set()
    Identity = id(Value)
    if Identity in Active:
        raise TypeError(f"{Path} contains a cyclic value")
    Active.add(Identity)
    try:
        if isinstance(Value, Mapping):
            Items = []
            SeenKeys: set[str] = set()
            for RawKey, RawValue in Value.items():
                if type(RawKey) is not str or not RawKey or RawKey in SeenKeys:
                    raise TypeError(
                        f"{Path} requires unique nonempty exact string keys"
                    )
                SeenKeys.add(RawKey)
                Items.append((
                    RawKey,
                    _FreezeAuthorityValue(
                        RawValue,
                        Path=f"{Path}.{RawKey}",
                        Active=Active,
                    ),
                ))
            return tuple(sorted(Items, key=lambda Item: Item[0]))
        if type(Value) in (tuple, list):
            return tuple(
                _FreezeAuthorityValue(
                    Item,
                    Path=f"{Path}[{Index}]",
                    Active=Active,
                )
                for Index, Item in enumerate(Value)
            )
        if type(Value) in (set, frozenset):
            Frozen = tuple(
                _FreezeAuthorityValue(Item, Path=f"{Path}[]", Active=Active)
                for Item in Value
            )
            return tuple(sorted(Frozen, key=BuildStableFingerprint))
        if is_dataclass(Value) and not isinstance(Value, type):
            return (
                ("__Type__", f"{type(Value).__module__}.{type(Value).__qualname__}"),
                *tuple(
                    (
                        Field.name,
                        _FreezeAuthorityValue(
                            getattr(Value, Field.name),
                            Path=f"{Path}.{Field.name}",
                            Active=Active,
                        ),
                    )
                    for Field in fields(Value)
                ),
            )
        ToDictionary = getattr(Value, "ToDictionary", None)
        if callable(ToDictionary):
            return (
                ("__Type__", f"{type(Value).__module__}.{type(Value).__qualname__}"),
                ("Value", _FreezeAuthorityValue(
                    ToDictionary(), Path=f"{Path}.ToDictionary", Active=Active,
                )),
            )
    finally:
        Active.remove(Identity)
    raise TypeError(f"{Path} contains unsupported {type(Value).__name__}")


def _AuthorityDictionary(Value: object) -> object:
    if isinstance(Value, tuple):
        if all(
            type(Item) is tuple
            and len(Item) == 2
            and type(Item[0]) is str
            for Item in Value
        ):
            return {
                Item[0]: _AuthorityDictionary(Item[1])
                for Item in Value
            }
        return [_AuthorityDictionary(Item) for Item in Value]
    return Value


@dataclass(frozen=True)
class CurrentSelectedAccessPolicySnapshot:
    """Complete policy plus the access controls used by the current gate."""

    Policy: PhysicalDesignPolicy
    PolicyPayload: tuple[tuple[str, object], ...]
    PolicyVersion: str
    PlacementAccessEnabled: bool
    CatalogVersion: str
    EnabledPatternFamilies: tuple[str, ...]
    MaximumDomainGenerationWork: int
    MaximumAssignmentExpansions: int
    TrackAssignmentMaximumExpansions: int
    RuntimeBudgetSeconds: float

    @property
    def PolicyFingerprint(self) -> str:
        return BuildStableFingerprint(_AuthorityDictionary(self.PolicyPayload))

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Policy": _AuthorityDictionary(self.PolicyPayload),
            "PolicyFingerprint": self.PolicyFingerprint,
            "PolicyVersion": self.PolicyVersion,
            "PlacementAccessEnabled": self.PlacementAccessEnabled,
            "CatalogVersion": self.CatalogVersion,
            "EnabledPatternFamilies": list(self.EnabledPatternFamilies),
            "MaximumDomainGenerationWork": self.MaximumDomainGenerationWork,
            "MaximumAssignmentExpansions": self.MaximumAssignmentExpansions,
            "TrackAssignmentMaximumExpansions": (
                self.TrackAssignmentMaximumExpansions
            ),
            "RuntimeBudgetSeconds": self.RuntimeBudgetSeconds,
        }


@dataclass(frozen=True)
class CurrentSelectedAccessSolveBinding:
    """Immutable producer-time association of policy and exact solve facts."""

    Policy: CurrentSelectedAccessPolicySnapshot
    SolveResult: PlacementAccessSolveResult
    SolveResultPayload: tuple[tuple[str, object], ...]
    ProblemFingerprint: str
    WitnessFingerprint: str | None
    SchemaVersion: str = "current-selected-access-solve-binding-v1"

    def __post_init__(self) -> None:
        if self.SchemaVersion != "current-selected-access-solve-binding-v1":
            raise ValueError("unsupported current selected-access solve binding")
        if type(self.Policy) is not CurrentSelectedAccessPolicySnapshot:
            raise TypeError("solve binding requires an exact policy snapshot")
        if type(self.SolveResult) is not PlacementAccessSolveResult:
            raise TypeError("solve binding requires an exact solve result")
        FrozenSolve = _FreezeAuthorityValue(
            self.SolveResult,
            Path="SolveBinding.SolveResult",
        )
        if (
            type(FrozenSolve) is not tuple
            or self.SolveResultPayload != FrozenSolve
            or self.ProblemFingerprint != self.SolveResult.ProblemFingerprint
            or self.WitnessFingerprint
            != (
                self.SolveResult.SelectedWitness.WitnessFingerprint
                if self.SolveResult.SelectedWitness is not None
                else None
            )
            or self.SolveResult.PolicyVersion != self.Policy.PolicyVersion
        ):
            raise ValueError("solve binding integrity is invalid")

    @property
    def BindingFingerprint(self) -> str:
        return BuildStableFingerprint(self.ToDictionary(include_fingerprint=False))

    def ToDictionary(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        Result = {
            "SchemaVersion": self.SchemaVersion,
            "Policy": self.Policy.ToDictionary(),
            "SolveResult": _AuthorityDictionary(self.SolveResultPayload),
            "ProblemFingerprint": self.ProblemFingerprint,
            "WitnessFingerprint": self.WitnessFingerprint,
        }
        if include_fingerprint:
            Result["BindingFingerprint"] = self.BindingFingerprint
        return Result


def BuildCurrentSelectedAccessSolveBinding(
    Policy: PhysicalDesignPolicy,
    SolveResult: PlacementAccessSolveResult,
) -> CurrentSelectedAccessSolveBinding:
    """Capture one immutable binding contemporaneously with solve production."""
    if type(Policy) is not PhysicalDesignPolicy:
        raise TypeError("solve binding policy must be exact")
    if type(SolveResult) is not PlacementAccessSolveResult:
        raise TypeError("solve binding result must be exact")
    OwnedSolve = deepcopy(SolveResult)
    FrozenSolve = _FreezeAuthorityValue(
        OwnedSolve,
        Path="SolveBinding.SolveResult",
    )
    if type(FrozenSolve) is not tuple:
        raise TypeError("solve binding result payload is invalid")
    return CurrentSelectedAccessSolveBinding(
        Policy=_PolicySnapshot(Policy),
        SolveResult=OwnedSolve,
        SolveResultPayload=FrozenSolve,
        ProblemFingerprint=OwnedSolve.ProblemFingerprint,
        WitnessFingerprint=(
            OwnedSolve.SelectedWitness.WitnessFingerprint
            if OwnedSolve.SelectedWitness is not None
            else None
        ),
    )


def SelectUnambiguousCurrentSelectedAccessSolveBinding(
    Bindings: tuple[CurrentSelectedAccessSolveBinding, ...],
    SolveResult: PlacementAccessSolveResult,
) -> CurrentSelectedAccessSolveBinding | None:
    """Select only one exact producer association; collisions stay ambiguous."""
    if type(Bindings) is not tuple:
        raise TypeError("solve binding collection must be an exact tuple")
    if type(SolveResult) is not PlacementAccessSolveResult:
        return None
    FrozenSolve = _FreezeAuthorityValue(
        SolveResult,
        Path="SolveBinding.LookupResult",
    )
    Matches = tuple(
        Binding
        for Binding in Bindings
        if (
            type(Binding) is CurrentSelectedAccessSolveBinding
            and Binding.SolveResultPayload == FrozenSolve
        )
    )
    return Matches[0] if len(Matches) == 1 else None


@dataclass(frozen=True)
class CurrentSelectedAccessCandidateSnapshot:
    """Complete phase-local candidate inputs without importing its owner type."""

    CandidateId: str
    SourceGenerator: str
    RoutingSpacing: int
    PlacementFingerprint: str
    PlacementRetentionFingerprint: str
    ObservedPlacementFingerprint: str
    ObservedPlacementRetentionFingerprint: str
    PlacementFingerprintIncludesLocalClaims: bool
    PlacementTransitionBasePayload: tuple[tuple[str, object], ...]
    PlacementCorePayload: tuple[tuple[str, object], ...]
    PlacementPayload: tuple[tuple[str, object], ...]

    @property
    def CandidateFingerprint(self) -> str:
        return BuildStableFingerprint(self.ToDictionary(include_fingerprint=False))

    def ToDictionary(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        Result = {
            "CandidateId": self.CandidateId,
            "SourceGenerator": self.SourceGenerator,
            "RoutingSpacing": self.RoutingSpacing,
            "PlacementFingerprint": self.PlacementFingerprint,
            "PlacementRetentionFingerprint": self.PlacementRetentionFingerprint,
            "ObservedPlacementFingerprint": self.ObservedPlacementFingerprint,
            "ObservedPlacementRetentionFingerprint": (
                self.ObservedPlacementRetentionFingerprint
            ),
            "PlacementFingerprintIncludesLocalClaims": (
                self.PlacementFingerprintIncludesLocalClaims
            ),
            "PlacementTransitionBase": _AuthorityDictionary(
                self.PlacementTransitionBasePayload
            ),
            "PlacementCore": _AuthorityDictionary(self.PlacementCorePayload),
            "Placement": _AuthorityDictionary(self.PlacementPayload),
        }
        if include_fingerprint:
            Result["CandidateSnapshotFingerprint"] = self.CandidateFingerprint
        return Result


@dataclass(frozen=True)
class CurrentSelectedAccessRoutingSnapshot:
    """Exact envelope, bounds, and assignment facts available at one phase."""

    RoutingEnvelope: DerivedRoutingEnvelope
    RoutingEnvelopePayload: tuple[tuple[str, object], ...]
    InclusiveRoutingBoundsXZ: tuple[int, int, int, int]
    LogicalRoutingLayers: tuple[int, ...]
    TrackPreparation: TrackAssignmentPreparation | None
    TrackPreparationPayload: tuple[tuple[str, object], ...] | None
    TrackPreparationAuthorityPayload: tuple[tuple[str, object], ...] | None
    RawTrackAssignmentApplicable: bool
    RawTrackAssignment: RawTrackAssignmentSelection | None
    RawTrackAssignmentPayload: tuple[tuple[str, object], ...] | None
    RawTrackAssignmentAuthorityFingerprint: str
    RequiredFields: tuple[str, ...]
    UnavailableFields: tuple[str, ...]
    OutOfScopeFields: tuple[str, ...]

    @property
    def RoutingInputFingerprint(self) -> str:
        return BuildStableFingerprint(self.ToDictionary())

    def ToDictionary(self) -> dict[str, object]:
        return {
            "RoutingEnvelope": _AuthorityDictionary(self.RoutingEnvelopePayload),
            "InclusiveRoutingBoundsXZ": list(self.InclusiveRoutingBoundsXZ),
            "LogicalRoutingLayers": list(self.LogicalRoutingLayers),
            "TrackPreparation": (
                _AuthorityDictionary(self.TrackPreparationPayload)
                if self.TrackPreparationPayload is not None
                else None
            ),
            "TrackPreparationAuthority": (
                _AuthorityDictionary(self.TrackPreparationAuthorityPayload)
                if self.TrackPreparationAuthorityPayload is not None
                else None
            ),
            "RawTrackAssignmentApplicable": self.RawTrackAssignmentApplicable,
            "RawTrackAssignmentType": (
                type(self.RawTrackAssignment).__name__
                if self.RawTrackAssignment is not None
                else None
            ),
            "RawTrackAssignment": (
                _AuthorityDictionary(self.RawTrackAssignmentPayload)
                if self.RawTrackAssignmentPayload is not None
                else None
            ),
            "RawTrackAssignmentAuthorityFingerprint": (
                self.RawTrackAssignmentAuthorityFingerprint
            ),
            "RequiredFields": list(self.RequiredFields),
            "UnavailableFields": list(self.UnavailableFields),
            "OutOfScopeFields": list(self.OutOfScopeFields),
        }


@dataclass(frozen=True)
class CurrentSelectedAccessPhaseObservation:
    """Owned observation retained by Ready and non-Ready outcomes alike."""

    Phase: CurrentSelectedAccessEnvelopePhase
    Transition: CurrentSelectedAccessTransition
    CandidateId: str
    PlacementFingerprint: str
    PlacementFingerprintIncludesLocalClaims: bool | None
    PhysicalValidation: CurrentSelectedPlacementAccessValidation | None
    JointInputFingerprint: str
    PredecessorEnvelopeFingerprint: str
    Detail: str

    def ToDictionary(self) -> dict[str, object]:
        return {
            "Phase": self.Phase.value,
            "Transition": self.Transition.value,
            "CandidateId": self.CandidateId,
            "PlacementFingerprint": self.PlacementFingerprint,
            "PlacementFingerprintIncludesLocalClaims": (
                self.PlacementFingerprintIncludesLocalClaims
            ),
            "PhysicalValidation": (
                self.PhysicalValidation.ToDictionary()
                if self.PhysicalValidation is not None
                else None
            ),
            "JointInputFingerprint": self.JointInputFingerprint,
            "PredecessorEnvelopeFingerprint": self.PredecessorEnvelopeFingerprint,
            "Detail": self.Detail,
        }


@dataclass(frozen=True)
class CurrentSelectedAccessEnvelope:
    """Usable immutable authority emitted only by a fresh Ready gate."""

    Phase: CurrentSelectedAccessEnvelopePhase
    Transition: CurrentSelectedAccessTransition
    PhysicalValidation: CurrentSelectedPlacementAccessValidation
    SolveBinding: CurrentSelectedAccessSolveBinding
    SolveResult: PlacementAccessSolveResult
    SelectedWitness: SelectedPlacementPinAccessWitness
    Domains: tuple[PlacedPinAccessOptionDomain, ...]
    Policy: CurrentSelectedAccessPolicySnapshot
    Candidate: CurrentSelectedAccessCandidateSnapshot
    Routing: CurrentSelectedAccessRoutingSnapshot
    TransitionSourcePlacementPayload: tuple[tuple[str, object], ...] | None = None
    ChannelPlacementPayload: tuple[tuple[str, object], ...] | None = None
    TransitionDeckPlacementPayload: tuple[tuple[str, object], ...] | None = None
    TransitionState: ClusterInterfacePlacementState | None = None
    PredecessorEnvelopeFingerprint: str = ""
    SchemaVersion: str = "current-selected-access-envelope-v1"

    def __post_init__(self) -> None:
        if self.SchemaVersion != "current-selected-access-envelope-v1":
            raise ValueError("unsupported current selected-access envelope schema")
        ExpectedTransitions = {
            CurrentSelectedAccessEnvelopePhase.BeforeRawMaterialization: {
                CurrentSelectedAccessTransition.InitialCandidate,
            },
            CurrentSelectedAccessEnvelopePhase.SelectedTrackSuccessor: {
                CurrentSelectedAccessTransition.SelectedTrackAssignment,
                CurrentSelectedAccessTransition.ChannelReplacement,
            },
            CurrentSelectedAccessEnvelopePhase.BeforePublication: {
                CurrentSelectedAccessTransition.PostRoutingCompaction,
            },
        }
        if self.Transition not in ExpectedTransitions[self.Phase]:
            raise ValueError("current selected-access phase transition is invalid")
        if (
            self.PhysicalValidation.Status
            is not CurrentSelectedPlacementAccessValidationStatus.Verified
            or self.PhysicalValidation.Reason
            is not CurrentSelectedPlacementAccessValidationReason.Current
        ):
            raise ValueError("usable access envelope requires Verified/Current")
        if type(self.SolveBinding) is not CurrentSelectedAccessSolveBinding:
            raise ValueError("usable access envelope requires a producer solve binding")
        if self.SolveResult.Status is not PlacementAccessSolveStatus.Feasible:
            raise ValueError("usable access envelope requires a feasible solve")
        if self.SolveResult.SelectedWitness != self.SelectedWitness:
            raise ValueError("usable access envelope witness differs from its solve")
        if self.SolveResult.Domains != self.Domains:
            raise ValueError("usable access envelope domains differ from its solve")
        if not self.Domains or any(
            type(Domain) is not PlacedPinAccessOptionDomain
            for Domain in self.Domains
        ):
            raise ValueError("usable access envelope requires exact domain evidence")
        if self.Phase is CurrentSelectedAccessEnvelopePhase.BeforeRawMaterialization:
            if self.PredecessorEnvelopeFingerprint:
                raise ValueError("initial access envelope cannot have a predecessor")
        elif not self.PredecessorEnvelopeFingerprint:
            raise ValueError("successor access envelope requires a predecessor")
        HasTransitionPayload = any(Value is not None for Value in (
            self.TransitionSourcePlacementPayload,
            self.ChannelPlacementPayload,
            self.TransitionDeckPlacementPayload,
            self.TransitionState,
        ))
        if self.Transition is CurrentSelectedAccessTransition.ChannelReplacement:
            if not all(Value is not None for Value in (
                self.TransitionSourcePlacementPayload,
                self.ChannelPlacementPayload,
                self.TransitionDeckPlacementPayload,
                self.TransitionState,
            )):
                raise ValueError("channel successor requires complete transition evidence")
        elif HasTransitionPayload:
            raise ValueError("unchanged access phase cannot carry transition evidence")

    @property
    def SelectedAccessEvidenceFingerprint(self) -> str:
        return BuildStableFingerprint({
            "SolveBinding": self.SolveBinding.ToDictionary(),
            "SolveResult": self.SolveResult.ToDictionary(),
            "SelectedWitness": self.SelectedWitness.ToDictionary(),
            "Domains": [Domain.ToDictionary() for Domain in self.Domains],
            "Policy": self.Policy.ToDictionary(),
            "TechnologyFingerprint": (
                self.PhysicalValidation.InputIdentity.TechnologyFingerprint
            ),
        })

    @property
    def CurrentPhaseInputFingerprint(self) -> str:
        return BuildStableFingerprint({
            "PhysicalValidation": self.PhysicalValidation.ToDictionary(),
            "Candidate": self.Candidate.ToDictionary(),
            "Routing": self.Routing.ToDictionary(),
            "TransitionSourcePlacement": (
                _AuthorityDictionary(self.TransitionSourcePlacementPayload)
                if self.TransitionSourcePlacementPayload is not None
                else None
            ),
            "ChannelPlacement": (
                _AuthorityDictionary(self.ChannelPlacementPayload)
                if self.ChannelPlacementPayload is not None
                else None
            ),
            "TransitionDeckPlacement": (
                _AuthorityDictionary(self.TransitionDeckPlacementPayload)
                if self.TransitionDeckPlacementPayload is not None
                else None
            ),
            "TransitionState": (
                self.TransitionState.ToDictionary()
                if self.TransitionState is not None
                else None
            ),
            "SelectedAccessEvidenceFingerprint": (
                self.SelectedAccessEvidenceFingerprint
            ),
        })

    @property
    def EnvelopeFingerprint(self) -> str:
        return BuildStableFingerprint(self.ToDictionary(include_fingerprint=False))

    def ToDictionary(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        Result = {
            "SchemaVersion": self.SchemaVersion,
            "Phase": self.Phase.value,
            "Transition": self.Transition.value,
            "PhysicalValidation": self.PhysicalValidation.ToDictionary(),
            "SolveBinding": self.SolveBinding.ToDictionary(),
            "SolveResult": self.SolveResult.ToDictionary(),
            "SelectedWitness": self.SelectedWitness.ToDictionary(),
            "Domains": [Domain.ToDictionary() for Domain in self.Domains],
            "Policy": self.Policy.ToDictionary(),
            "Candidate": self.Candidate.ToDictionary(),
            "Routing": self.Routing.ToDictionary(),
            "TransitionSourcePlacement": (
                _AuthorityDictionary(self.TransitionSourcePlacementPayload)
                if self.TransitionSourcePlacementPayload is not None
                else None
            ),
            "ChannelPlacement": (
                _AuthorityDictionary(self.ChannelPlacementPayload)
                if self.ChannelPlacementPayload is not None
                else None
            ),
            "TransitionDeckPlacement": (
                _AuthorityDictionary(self.TransitionDeckPlacementPayload)
                if self.TransitionDeckPlacementPayload is not None
                else None
            ),
            "TransitionState": (
                self.TransitionState.ToDictionary()
                if self.TransitionState is not None
                else None
            ),
            "PredecessorEnvelopeFingerprint": self.PredecessorEnvelopeFingerprint,
            "SelectedAccessEvidenceFingerprint": (
                self.SelectedAccessEvidenceFingerprint
            ),
            "CurrentPhaseInputFingerprint": self.CurrentPhaseInputFingerprint,
        }
        if include_fingerprint:
            Result["EnvelopeFingerprint"] = self.EnvelopeFingerprint
        return Result


@dataclass(frozen=True)
class CurrentSelectedAccessEnvelopeResult:
    """Typed gate result; non-Ready outcomes cannot retain usable authority."""

    Status: CurrentSelectedAccessEnvelopeStatus
    Reason: CurrentSelectedAccessEnvelopeReason
    Observations: tuple[CurrentSelectedAccessPhaseObservation, ...]
    Envelope: CurrentSelectedAccessEnvelope | None = None
    SchemaVersion: str = "current-selected-access-envelope-result-v1"

    def __post_init__(self) -> None:
        if self.SchemaVersion != "current-selected-access-envelope-result-v1":
            raise ValueError("unsupported current selected-access result schema")
        if type(self.Status) is not CurrentSelectedAccessEnvelopeStatus:
            raise TypeError("current selected-access result status must be typed")
        if type(self.Reason) is not CurrentSelectedAccessEnvelopeReason:
            raise TypeError("current selected-access result reason must be typed")
        if not self.Observations or any(
            type(Value) is not CurrentSelectedAccessPhaseObservation
            for Value in self.Observations
        ):
            raise ValueError("current selected-access result requires observations")
        if self.Status is CurrentSelectedAccessEnvelopeStatus.Ready:
            if self.Reason is not CurrentSelectedAccessEnvelopeReason.Current:
                raise ValueError("Ready current selected-access result requires Current")
            if type(self.Envelope) is not CurrentSelectedAccessEnvelope:
                raise ValueError("Ready current selected-access result requires envelope")
        elif self.Envelope is not None:
            raise ValueError("non-Ready current selected-access result cannot carry envelope")

    def ToDictionary(self) -> dict[str, object]:
        return {
            "SchemaVersion": self.SchemaVersion,
            "Status": self.Status.value,
            "Reason": self.Reason.value,
            "Observations": [Value.ToDictionary() for Value in self.Observations],
            "Envelope": self.Envelope.ToDictionary() if self.Envelope else None,
            "HistoricalOnly": True,
        }


def _PlacementPayload(Placement: PcbPlacement) -> tuple[tuple[str, object], ...]:
    Value = {
        "PlacedGates": tuple(Placement.Placed.PlacedGates),
        "RouteGuides": Placement.Placed.RouteGuides,
        "RouteLayers": Placement.Placed.RouteLayers,
        "FrozenNetWires": Placement.Placed.FrozenNetWires or {},
        "LocalNetBranches": Placement.Placed.LocalNetBranches or {},
        "LocalNetTargets": Placement.Placed.LocalNetTargets or {},
        "LocalRouteClaims": Placement.Placed.LocalRouteClaims or (),
        "PlacedClusterBoundaryLeaseRequests": (
            Placement.Placed.ClusterBoundaryLeaseRequests
        ),
        "PlacedCompleteClusterInterfaceAccess": (
            Placement.Placed.CompleteClusterInterfaceAccess
        ),
        "PlacedInterClusterRoutingChannel": (
            Placement.Placed.InterClusterRoutingChannel
        ),
        "PlacedPlacementAccessFabric": Placement.Placed.PlacementAccessFabric,
        "PlacedPlacementAccessAssignment": (
            Placement.Placed.PlacementAccessAssignment
        ),
        "PlacedDerivedPerimeterSlotDomain": (
            Placement.Placed.DerivedPerimeterSlotDomain
        ),
        "PlacedDerivedPerimeterSlotAssignment": (
            Placement.Placed.DerivedPerimeterSlotAssignment
        ),
        "PlacedDerivedLocalRouteClaims": (
            Placement.Placed.DerivedLocalRouteClaims
        ),
        "PlacedRoutedComponentTemplates": (
            Placement.Placed.RoutedComponentTemplates
        ),
        "PlacedRoutedComponentRoutingChannels": (
            Placement.Placed.RoutedComponentRoutingChannels
        ),
        "PlacedPackedClusters": Placement.Placed.PackedClusters,
        "PlacedComponentGraph": Placement.Placed.ComponentGraph,
        "Clusters": Placement.Clusters,
        "SignalOrder": Placement.SignalOrder,
        "LayerCount": Placement.LayerCount,
        "PackedClusters": Placement.PackedClusters,
        "ClusterBoundaryLeaseRequests": Placement.ClusterBoundaryLeaseRequests,
        "ClusterLocalRouteTemplates": Placement.ClusterLocalRouteTemplates,
        "ClusterBoundaryLeaseVariant": Placement.ClusterBoundaryLeaseVariant,
        "CompleteClusterInterfaceAccess": Placement.CompleteClusterInterfaceAccess,
        "MandatoryAccessPreScreenProfile": (
            Placement.MandatoryAccessPreScreenProfile
        ),
        "InterClusterRoutingChannel": Placement.InterClusterRoutingChannel,
        "PlacementAccessFabric": Placement.PlacementAccessFabric,
        "PlacementAccessAssignment": Placement.PlacementAccessAssignment,
        "DerivedPerimeterSlotDomain": Placement.DerivedPerimeterSlotDomain,
        "DerivedPerimeterSlotAssignment": Placement.DerivedPerimeterSlotAssignment,
        "DerivedLocalRouteClaims": Placement.DerivedLocalRouteClaims,
        "ComponentGraph": Placement.ComponentGraph,
    }
    Frozen = _FreezeAuthorityValue(Value, Path="Placement")
    if type(Frozen) is not tuple:
        raise TypeError("placement authority payload is invalid")
    return Frozen


def _PlacementCorePayload(Placement: PcbPlacement) -> tuple[tuple[str, object], ...]:
    """Freeze placement identity while excluding later access attachment facts."""
    Value = {
        "PlacedGates": tuple(Placement.Placed.PlacedGates),
        "RouteGuides": Placement.Placed.RouteGuides,
        "RouteLayers": Placement.Placed.RouteLayers,
        "FrozenNetWires": Placement.Placed.FrozenNetWires or {},
        "LocalNetBranches": Placement.Placed.LocalNetBranches or {},
        "LocalNetTargets": Placement.Placed.LocalNetTargets or {},
        "LocalRouteClaims": Placement.Placed.LocalRouteClaims or (),
        "PlacedClusterBoundaryLeaseRequests": (
            Placement.Placed.ClusterBoundaryLeaseRequests
        ),
        "PlacedCompleteClusterInterfaceAccess": (
            Placement.Placed.CompleteClusterInterfaceAccess
        ),
        "PlacedInterClusterRoutingChannel": (
            Placement.Placed.InterClusterRoutingChannel
        ),
        "PlacedDerivedPerimeterSlotDomain": (
            Placement.Placed.DerivedPerimeterSlotDomain
        ),
        "PlacedDerivedPerimeterSlotAssignment": (
            Placement.Placed.DerivedPerimeterSlotAssignment
        ),
        "PlacedDerivedLocalRouteClaims": (
            Placement.Placed.DerivedLocalRouteClaims
        ),
        "PlacedRoutedComponentTemplates": (
            Placement.Placed.RoutedComponentTemplates
        ),
        "PlacedRoutedComponentRoutingChannels": (
            Placement.Placed.RoutedComponentRoutingChannels
        ),
        "PlacedPackedClusters": Placement.Placed.PackedClusters,
        "PlacedComponentGraph": Placement.Placed.ComponentGraph,
        "Clusters": Placement.Clusters,
        "SignalOrder": Placement.SignalOrder,
        "LayerCount": Placement.LayerCount,
        "PackedClusters": Placement.PackedClusters,
        "ClusterBoundaryLeaseRequests": Placement.ClusterBoundaryLeaseRequests,
        "ClusterLocalRouteTemplates": Placement.ClusterLocalRouteTemplates,
        "ClusterBoundaryLeaseVariant": Placement.ClusterBoundaryLeaseVariant,
        "CompleteClusterInterfaceAccess": Placement.CompleteClusterInterfaceAccess,
        "MandatoryAccessPreScreenProfile": (
            Placement.MandatoryAccessPreScreenProfile
        ),
        "InterClusterRoutingChannel": Placement.InterClusterRoutingChannel,
        "DerivedPerimeterSlotDomain": Placement.DerivedPerimeterSlotDomain,
        "DerivedPerimeterSlotAssignment": Placement.DerivedPerimeterSlotAssignment,
        "DerivedLocalRouteClaims": Placement.DerivedLocalRouteClaims,
        "ComponentGraph": Placement.ComponentGraph,
    }
    Frozen = _FreezeAuthorityValue(Value, Path="PlacementCore")
    if type(Frozen) is not tuple:
        raise TypeError("placement core authority payload is invalid")
    return Frozen


def _PlacementTransitionBasePayload(
    Placement: PcbPlacement,
) -> tuple[tuple[str, object], ...]:
    """Freeze fields which local-route materialization may not change."""
    Value = {
        "PlacedGates": tuple(Placement.Placed.PlacedGates),
        "PlacedClusterBoundaryLeaseRequests": (
            Placement.Placed.ClusterBoundaryLeaseRequests
        ),
        "PlacedCompleteClusterInterfaceAccess": (
            Placement.Placed.CompleteClusterInterfaceAccess
        ),
        "PlacedInterClusterRoutingChannel": (
            Placement.Placed.InterClusterRoutingChannel
        ),
        "PlacedPlacementAccessFabric": Placement.Placed.PlacementAccessFabric,
        "PlacedPlacementAccessAssignment": (
            Placement.Placed.PlacementAccessAssignment
        ),
        "PlacedDerivedPerimeterSlotDomain": (
            Placement.Placed.DerivedPerimeterSlotDomain
        ),
        "PlacedDerivedPerimeterSlotAssignment": (
            Placement.Placed.DerivedPerimeterSlotAssignment
        ),
        "Clusters": Placement.Clusters,
        "SignalOrder": Placement.SignalOrder,
        "LayerCount": Placement.LayerCount,
        "PackedClusters": Placement.PackedClusters,
        "ClusterBoundaryLeaseRequests": Placement.ClusterBoundaryLeaseRequests,
        "ClusterBoundaryLeaseVariant": Placement.ClusterBoundaryLeaseVariant,
        "CompleteClusterInterfaceAccess": Placement.CompleteClusterInterfaceAccess,
        "MandatoryAccessPreScreenProfile": (
            Placement.MandatoryAccessPreScreenProfile
        ),
        "InterClusterRoutingChannel": Placement.InterClusterRoutingChannel,
        "PlacementAccessFabric": Placement.PlacementAccessFabric,
        "PlacementAccessAssignment": Placement.PlacementAccessAssignment,
        "SelectedPinAccessWitness": Placement.SelectedPinAccessWitness,
        "PlacementAccessSolve": Placement.PlacementAccessSolve,
        "DerivedPerimeterSlotDomain": Placement.DerivedPerimeterSlotDomain,
        "DerivedPerimeterSlotAssignment": Placement.DerivedPerimeterSlotAssignment,
        "ComponentGraph": Placement.ComponentGraph,
    }
    Frozen = _FreezeAuthorityValue(Value, Path="PlacementTransitionBase")
    if type(Frozen) is not tuple:
        raise TypeError("placement transition base payload is invalid")
    return Frozen


def _TranslatePosition(
    Position: tuple[int, int, int] | None,
    Delta: tuple[int, int, int],
) -> tuple[int, int, int] | None:
    if Position is None:
        return None
    return tuple(Position[Index] + Delta[Index] for Index in range(3))


def _ChannelPlacementMatchesSource(
    Source: PcbPlacement,
    ChannelPlacement: PcbPlacement,
) -> bool:
    """Check the exact documented source-to-channel placement delta."""
    if type(Source) is not PcbPlacement or type(ChannelPlacement) is not PcbPlacement:
        raise TypeError("channel transition placements must be exact PcbPlacement values")
    Channel = ChannelPlacement.InterClusterRoutingChannel
    if (
        type(Channel) is not InterClusterRoutingChannel
        or ChannelPlacement.Placed.InterClusterRoutingChannel != Channel
    ):
        return False
    Translations = dict(Channel.ClusterTranslations)
    if (
        len(Translations) != len(Channel.ClusterTranslations)
        or any(type(Cluster) is not int for Cluster in Translations)
        or not set(Translations) <= set(range(len(Source.Clusters)))
        or any(
            type(Delta) is not tuple
            or len(Delta) != 3
            or any(type(Value) is not int for Value in Delta)
            for Delta in Translations.values()
        )
    ):
        return False
    ClusterByGate = {
        GateName: ClusterIndex
        for ClusterIndex, GateNames in enumerate(Source.Clusters)
        for GateName in GateNames
    }
    SourceGates = {Gate.Name: Gate for Gate in Source.Placed.PlacedGates}
    TargetGates = {
        Gate.Name: Gate for Gate in ChannelPlacement.Placed.PlacedGates
    }
    if set(SourceGates) != set(TargetGates):
        return False
    for GateName, SourceGate in SourceGates.items():
        Delta = Translations.get(ClusterByGate.get(GateName), (0, 0, 0))
        ExpectedGate = replace(
            SourceGate,
            X=SourceGate.X + Delta[0],
            Y=SourceGate.Y + Delta[1],
            Z=SourceGate.Z + Delta[2],
            InputPins=[
                _TranslatePosition(Position, Delta)
                for Position in SourceGate.InputPins
            ],
            OutputPin=_TranslatePosition(SourceGate.OutputPin, Delta),
        )
        if TargetGates[GateName] != ExpectedGate:
            return False
    ExpectedClaims = tuple(
        TranslateClusterLocalRouteClaim(
            Claim,
            Translations.get(Claim.ClusterId, (0, 0, 0)),
        )
        for Claim in Source.Placed.LocalRouteClaims or ()
    )
    if ChannelPlacement.Placed.LocalRouteClaims != ExpectedClaims:
        return False
    SourceRequests = BuildPhysicalClusterBoundaryLeaseRequests(Source)
    ExpectedRequests = tuple(
        replace(
            Request,
            SourceTerminal=_TranslatePosition(
                Request.SourceTerminal,
                Translations.get(Request.SourceCluster, (0, 0, 0)),
            ),
            TargetTerminals=tuple(
                _TranslatePosition(
                    Terminal,
                    Translations.get(Request.TargetCluster, (0, 0, 0)),
                )
                for Terminal in Request.TargetTerminals
            ),
        )
        for Request in SourceRequests
    )
    if (
        ChannelPlacement.ClusterBoundaryLeaseRequests != ExpectedRequests
        or ChannelPlacement.Placed.ClusterBoundaryLeaseRequests != ExpectedRequests
    ):
        return False
    ExpectedPackedClusters = tuple(
        replace(
            Cluster,
            BoundaryTerminals=tuple(
                _TranslatePosition(
                    Terminal,
                    Translations.get(Cluster.ClusterId, (0, 0, 0)),
                )
                for Terminal in Cluster.BoundaryTerminals
            ),
        )
        for Cluster in Source.PackedClusters
    )
    ExpectedTemplates = tuple(
        replace(
            Template,
            Origin=_TranslatePosition(
                Template.Origin,
                Translations.get(Template.ClusterId, (0, 0, 0)),
            ),
        )
        for Template in Source.ClusterLocalRouteTemplates
    )
    if (
        ChannelPlacement.PackedClusters != ExpectedPackedClusters
        or ChannelPlacement.ClusterLocalRouteTemplates != ExpectedTemplates
    ):
        return False
    AllowedPlacementFields = {
        "Placed",
        "PackedClusters",
        "ClusterBoundaryLeaseRequests",
        "ClusterLocalRouteTemplates",
        "CompleteClusterInterfaceAccess",
        "MandatoryAccessPreScreenProfile",
        "InterClusterRoutingChannel",
        "ComponentGraph",
        "SelectedPinAccessWitness",
        "PlacementAccessSolve",
    }
    if any(
        getattr(Source, Field.name) != getattr(ChannelPlacement, Field.name)
        for Field in fields(PcbPlacement)
        if Field.name not in AllowedPlacementFields
    ):
        return False
    AllowedPlacedFields = {
        "PlacedGates",
        "RouteGuides",
        "RouteLayers",
        "FrozenNetWires",
        "LocalNetBranches",
        "LocalNetTargets",
        "LocalRouteClaims",
        "LocalRouteDiagnostics",
        "ClusterBoundaryLeaseRequests",
        "CompleteClusterInterfaceAccess",
        "InterClusterRoutingChannel",
        "PlacementAccessFabric",
        "PlacementAccessAssignment",
        "SelectedPinAccessWitness",
        "PlacementAccessSolve",
        "DerivedPerimeterSlotDomain",
        "DerivedPerimeterSlotAssignment",
        "DerivedLocalRouteClaims",
        "RoutedComponentTemplates",
        "RoutedComponentRoutingChannels",
        "PackedClusters",
        "ComponentGraph",
    }
    if any(
        getattr(Source.Placed, Field.name)
        != getattr(ChannelPlacement.Placed, Field.name)
        for Field in fields(type(Source.Placed))
        if Field.name not in AllowedPlacedFields
    ):
        return False
    return (
        ChannelPlacement.Placed.Module == Source.Placed.Module
        and ChannelPlacement.Placed.RouteGuides is None
        and ChannelPlacement.Placed.RouteLayers is None
        and ChannelPlacement.Placed.FrozenNetWires is None
        and ChannelPlacement.Placed.LocalNetBranches is None
        and ChannelPlacement.Placed.LocalNetTargets is None
        and ChannelPlacement.Placed.PlacementAccessFabric is None
        and ChannelPlacement.Placed.PlacementAccessAssignment is None
        and ChannelPlacement.Placed.SelectedPinAccessWitness is None
        and ChannelPlacement.Placed.PlacementAccessSolve is None
        and ChannelPlacement.SelectedPinAccessWitness is None
        and ChannelPlacement.PlacementAccessSolve is None
        and ChannelPlacement.Placed.DerivedPerimeterSlotDomain is None
        and ChannelPlacement.Placed.DerivedPerimeterSlotAssignment is None
        and ChannelPlacement.Placed.DerivedLocalRouteClaims == ()
        and ChannelPlacement.Placed.RoutedComponentTemplates == ()
        and ChannelPlacement.Placed.RoutedComponentRoutingChannels == ()
        and ChannelPlacement.Placed.PackedClusters == Source.PackedClusters
        and ChannelPlacement.Placed.CompleteClusterInterfaceAccess is True
        and ChannelPlacement.Placed.ComponentGraph
        == ChannelPlacement.ComponentGraph
        and ChannelPlacement.ComponentGraph
        == (Source.ComponentGraph or Source.Placed.ComponentGraph)
        and ChannelPlacement.CompleteClusterInterfaceAccess is True
        and ChannelPlacement.MandatoryAccessPreScreenProfile is None
    )


def _DeckPlacementMatchesChannel(
    ChannelPlacement: PcbPlacement,
    Successor: PcbPlacement,
    State: ClusterInterfacePlacementState,
    *,
    PlacementFingerprint: str,
    InterfaceTopologyFingerprint: str,
) -> bool:
    """Check the exact documented channel-to-deck successor delta."""
    if (
        type(ChannelPlacement) is not PcbPlacement
        or type(Successor) is not PcbPlacement
        or type(State) is not ClusterInterfacePlacementState
    ):
        raise TypeError("channel successor relation requires exact typed inputs")
    Deck = Successor.InterClusterRoutingChannel
    if (
        type(Deck) is not InterClusterRoutingChannel
        or Successor.Placed.InterClusterRoutingChannel != Deck
        or State.InterClusterChannel != Deck
        or State.StateFingerprint != PlacementFingerprint
        or State.InterfaceTopologyFingerprint != InterfaceTopologyFingerprint
        or State.ChannelFingerprint != Deck.ChannelFingerprint
    ):
        return False
    AllowedPlacementFields = {
        "Placed",
        "ClusterBoundaryLeaseRequests",
        "CompleteClusterInterfaceAccess",
        "MandatoryAccessPreScreenProfile",
        "InterClusterRoutingChannel",
    }
    if any(
        getattr(ChannelPlacement, Field.name) != getattr(Successor, Field.name)
        for Field in fields(PcbPlacement)
        if Field.name not in AllowedPlacementFields
    ):
        return False
    ExpectedRequests = BuildPhysicalClusterBoundaryLeaseRequests(ChannelPlacement)
    if Successor.ClusterBoundaryLeaseRequests != ExpectedRequests:
        return False
    AllowedPlacedFields = {
        "RouteGuides",
        "RouteLayers",
        "FrozenNetWires",
        "LocalNetBranches",
        "LocalNetTargets",
        "LocalRouteDiagnostics",
        "ClusterBoundaryLeaseRequests",
        "CompleteClusterInterfaceAccess",
        "InterClusterRoutingChannel",
        "PackedClusters",
    }
    if any(
        getattr(ChannelPlacement.Placed, Field.name)
        != getattr(Successor.Placed, Field.name)
        for Field in fields(type(ChannelPlacement.Placed))
        if Field.name not in AllowedPlacedFields
    ):
        return False
    return (
        Successor.Placed.PlacedGates == ChannelPlacement.Placed.PlacedGates
        and Successor.Placed.LocalRouteClaims
        == ChannelPlacement.Placed.LocalRouteClaims
        and Successor.Placed.RouteGuides is None
        and Successor.Placed.RouteLayers is None
        and Successor.Placed.FrozenNetWires is None
        and Successor.Placed.LocalNetBranches is None
        and Successor.Placed.LocalNetTargets is None
        and Successor.Placed.ClusterBoundaryLeaseRequests == ExpectedRequests
        and Successor.Placed.CompleteClusterInterfaceAccess is True
        and Successor.Placed.PackedClusters == ChannelPlacement.PackedClusters
        and Successor.CompleteClusterInterfaceAccess is True
        and Successor.MandatoryAccessPreScreenProfile is None
    )


def _ReboundPlacementMatchesDeck(
    DeckPlacement: PcbPlacement,
    Current: PcbPlacement,
) -> bool:
    """Require a deck-identical placement with only an access-evidence delta."""
    if type(DeckPlacement) is not PcbPlacement or type(Current) is not PcbPlacement:
        raise TypeError("placement rebind inputs must be exact PcbPlacement values")
    AccessFields = {
        "PlacementAccessFabric",
        "PlacementAccessAssignment",
        "SelectedPinAccessWitness",
        "PlacementAccessSolve",
    }
    if any(
        getattr(DeckPlacement, Field.name) != getattr(Current, Field.name)
        for Field in fields(PcbPlacement)
        if Field.name not in AccessFields | {"Placed"}
    ):
        return False
    if any(
        getattr(DeckPlacement.Placed, Field.name)
        != getattr(Current.Placed, Field.name)
        for Field in fields(type(DeckPlacement.Placed))
        if Field.name not in AccessFields
    ):
        return False
    return (
        Current.PlacementAccessFabric is None
        and Current.PlacementAccessAssignment is None
        and Current.Placed.PlacementAccessFabric is None
        and Current.Placed.PlacementAccessAssignment is None
        and type(Current.PlacementAccessSolve) is PlacementAccessSolveResult
        and Current.Placed.PlacementAccessSolve == Current.PlacementAccessSolve
        and Current.Placed.SelectedPinAccessWitness
        == Current.SelectedPinAccessWitness
    )


def _PolicySnapshot(Policy: PhysicalDesignPolicy) -> CurrentSelectedAccessPolicySnapshot:
    if type(Policy) is not PhysicalDesignPolicy:
        raise TypeError("Policy must be an exact PhysicalDesignPolicy")
    Owned = deepcopy(Policy)
    Frozen = _FreezeAuthorityValue(Owned, Path="Policy")
    if type(Frozen) is not tuple:
        raise TypeError("policy authority payload is invalid")
    return CurrentSelectedAccessPolicySnapshot(
        Policy=Owned,
        PolicyPayload=Frozen,
        PolicyVersion=Owned.PolicyVersion,
        PlacementAccessEnabled=Owned.PlacementAccess.Enabled,
        CatalogVersion=Owned.PlacementAccess.CatalogVersion,
        EnabledPatternFamilies=tuple(Owned.PlacementAccess.EnabledPatternFamilies),
        MaximumDomainGenerationWork=(
            Owned.PlacementAccess.MaximumDomainGenerationWork
        ),
        MaximumAssignmentExpansions=(
            Owned.PlacementAccess.MaximumAssignmentExpansions
        ),
        TrackAssignmentMaximumExpansions=(
            Owned.TrackAssignment.MaximumAssignmentExpansions
        ),
        RuntimeBudgetSeconds=Owned.RuntimeBudgetSeconds,
    )


def _SolveBindingMatchesCurrent(
    Binding: CurrentSelectedAccessSolveBinding | None,
    SolveResult: PlacementAccessSolveResult,
    SelectedWitness: SelectedPlacementPinAccessWitness | None,
    Policy: PhysicalDesignPolicy,
) -> bool:
    """Validate exact producer association and the complete live policy."""
    if type(Binding) is not CurrentSelectedAccessSolveBinding:
        return False
    FrozenSolve = _FreezeAuthorityValue(
        SolveResult,
        Path="CurrentSolveBinding.SolveResult",
    )
    FrozenBoundSolve = _FreezeAuthorityValue(
        Binding.SolveResult,
        Path="CurrentSolveBinding.BoundSolveResult",
    )
    FrozenLivePolicy = _FreezeAuthorityValue(
        Policy,
        Path="CurrentSolveBinding.LivePolicy",
    )
    FrozenBoundPolicy = _FreezeAuthorityValue(
        Binding.Policy.Policy,
        Path="CurrentSolveBinding.BoundPolicy",
    )
    WitnessFingerprint = (
        SelectedWitness.WitnessFingerprint
        if SelectedWitness is not None
        else None
    )
    return (
        type(FrozenSolve) is tuple
        and type(FrozenBoundSolve) is tuple
        and type(FrozenLivePolicy) is tuple
        and type(FrozenBoundPolicy) is tuple
        and Binding.SolveResultPayload == FrozenBoundSolve == FrozenSolve
        and Binding.Policy.PolicyPayload == FrozenBoundPolicy == FrozenLivePolicy
        and Binding.ProblemFingerprint == SolveResult.ProblemFingerprint
        and Binding.WitnessFingerprint == WitnessFingerprint
        and Binding.SolveResult.PolicyVersion == SolveResult.PolicyVersion
        and SolveResult.PolicyVersion == Binding.Policy.PolicyVersion
        and SolveResult.PolicyVersion == Policy.PolicyVersion
        and bool(SolveResult.PolicyVersion)
    )


def _TrackPreparationAuthority(
    Preparation: TrackAssignmentPreparation,
) -> tuple[tuple[str, object], ...]:
    """Freeze the typed assignment fields without promoting diagnostics."""
    Frozen = _FreezeAuthorityValue({
        "Success": Preparation.Success,
        "SelectedCandidateIds": Preparation.SelectedCandidateIds,
        "CandidateCounts": Preparation.CandidateCounts,
        "ConflictSignals": Preparation.ConflictSignals,
        "ConflictResourceIndices": Preparation.ConflictResourceIndices,
        "ExpansionCount": Preparation.ExpansionCount,
        "Complete": Preparation.Complete,
        "IncompleteReason": Preparation.IncompleteReason,
        "SelectedLocalClaimChoiceIds": Preparation.SelectedLocalClaimChoiceIds,
        "LocalClaimDomainFingerprint": Preparation.LocalClaimDomainFingerprint,
        "CandidateDomainFingerprint": Preparation.CandidateDomainFingerprint,
        "SelectedCapacityResourceIds": Preparation.SelectedCapacityResourceIds,
        "PinAccessDomainFingerprint": Preparation.PinAccessDomainFingerprint,
        "PinAccessWitnessFingerprint": Preparation.PinAccessWitnessFingerprint,
        "PinAccessHandoffObservation": Preparation.PinAccessHandoffObservation,
    }, Path="TrackPreparationAuthority")
    if type(Frozen) is not tuple:
        raise TypeError("track preparation authority payload is invalid")
    return Frozen


def _RawTrackAssignmentAuthority(
    Raw: RawTrackAssignmentSelection,
    TrackPreparation: TrackAssignmentPreparation,
    *,
    CandidateId: str,
) -> tuple[tuple[str, object], ...] | None:
    """Validate one exact raw result and bind its authoritative preparation."""
    if type(Raw) is not RawTrackAssignmentSelection:
        raise TypeError("raw track assignment must be an exact selection")
    if type(Raw.Preparation) is not TrackAssignmentPreparation:
        return None
    if any(type(Value) is not bool for Value in (
        Raw.Success,
        Raw.Complete,
        Raw.Unsatisfiable,
    )):
        raise TypeError("raw track assignment terminal axes must be exact bools")
    if (
        not Raw.Success
        or not Raw.Complete
        or Raw.Unsatisfiable
        or Raw.IncompleteReason
        or type(Raw.ProblemFingerprint) is not str
        or not Raw.ProblemFingerprint
        or type(Raw.SelectionFingerprint) is not str
        or not Raw.SelectionFingerprint
        or type(Raw.SelectedTemplateId) is not str
        or Raw.SelectedTemplateId != CandidateId
        or type(Raw.Attempts) is not tuple
        or not Raw.Attempts
        or any(type(Value) is not RawTrackAssignmentAttempt for Value in Raw.Attempts)
        or not any(
            Attempt.TemplateId == Raw.SelectedTemplateId
            and Attempt.Success
            and Attempt.Complete
            for Attempt in Raw.Attempts
        )
    ):
        return None
    ExpectedSelectionFingerprint = BuildStableFingerprint({
        "ProblemFingerprint": Raw.ProblemFingerprint,
        "SelectedTemplateId": Raw.SelectedTemplateId,
        "Preparation": Raw.Preparation.ToDictionary(),
    })
    if Raw.SelectionFingerprint != ExpectedSelectionFingerprint:
        return None
    RawPreparationAuthority = _TrackPreparationAuthority(Raw.Preparation)
    TrackPreparationAuthority = _TrackPreparationAuthority(TrackPreparation)
    if RawPreparationAuthority != TrackPreparationAuthority:
        return None
    Frozen = _FreezeAuthorityValue({
        "ProblemFingerprint": Raw.ProblemFingerprint,
        "SelectedTemplateId": Raw.SelectedTemplateId,
        "SelectedObjective": Raw.SelectedObjective,
        "Preparation": _AuthorityDictionary(RawPreparationAuthority),
        "Attempts": Raw.Attempts,
        "ExpansionCount": Raw.ExpansionCount,
        "Success": Raw.Success,
        "Complete": Raw.Complete,
        "Unsatisfiable": Raw.Unsatisfiable,
        "IncompleteReason": Raw.IncompleteReason,
        "FirstConflictSignals": Raw.FirstConflictSignals,
        "FirstConflictResourceIndices": Raw.FirstConflictResourceIndices,
        "MaterializedTemplateCount": Raw.MaterializedTemplateCount,
        "SkippedDominatedTemplateCount": Raw.SkippedDominatedTemplateCount,
    }, Path="RawTrackAssignmentAuthority")
    if type(Frozen) is not tuple:
        raise TypeError("raw track assignment authority payload is invalid")
    return Frozen


def _NonReady(
    Status: CurrentSelectedAccessEnvelopeStatus,
    Reason: CurrentSelectedAccessEnvelopeReason,
    Observation: CurrentSelectedAccessPhaseObservation,
    Predecessor: CurrentSelectedAccessEnvelopeResult | None,
) -> CurrentSelectedAccessEnvelopeResult:
    History = (
        Predecessor.Observations
        if Predecessor is not None
        else ()
    )
    return CurrentSelectedAccessEnvelopeResult(
        Status=Status,
        Reason=Reason,
        Observations=(*History, Observation),
    )


def BuildCurrentSelectedAccessEnvelope(
    *,
    Phase: CurrentSelectedAccessEnvelopePhase,
    Transition: CurrentSelectedAccessTransition,
    CandidateId: str,
    SourceGenerator: str,
    RoutingSpacing: int,
    PlacementFingerprint: str,
    PlacementRetentionFingerprint: str,
    InterfaceTopologyFingerprint: str,
    ObservedPlacementFingerprint: str,
    ObservedPlacementRetentionFingerprint: str,
    PlacementFingerprintIncludesLocalClaims: bool | None,
    Placement: PcbPlacement,
    ResourceGraph: RoutingResourceGraph,
    Technology: RedstoneRoutingTechnology,
    Policy: PhysicalDesignPolicy,
    RoutingEnvelope: DerivedRoutingEnvelope | None,
    SolveBinding: CurrentSelectedAccessSolveBinding | None = None,
    TrackPreparation: TrackAssignmentPreparation | None = None,
    RawTrackAssignment: object | None = None,
    RawTrackAssignmentApplicable: bool = False,
    Predecessor: CurrentSelectedAccessEnvelopeResult | None = None,
    TransitionSourceCandidateId: str = "",
    TransitionSourcePlacementFingerprint: str = "",
    TransitionSourcePlacementRetentionFingerprint: str = "",
    TransitionSourcePlacementFingerprintIncludesLocalClaims: bool | None = None,
    TransitionSourcePlacement: PcbPlacement | None = None,
    ChannelPlacement: PcbPlacement | None = None,
    TransitionDeckPlacement: PcbPlacement | None = None,
    TransitionState: ClusterInterfacePlacementState | None = None,
) -> CurrentSelectedAccessEnvelopeResult:
    """Freshly build one phase-local current-selected-access result."""
    if type(Phase) is not CurrentSelectedAccessEnvelopePhase:
        raise TypeError("Phase must be a typed current selected-access phase")
    if type(Transition) is not CurrentSelectedAccessTransition:
        raise TypeError("Transition must be a typed current selected-access transition")
    if type(Placement) is not PcbPlacement:
        raise TypeError("Placement must be an exact PcbPlacement")
    if type(ResourceGraph) is not RoutingResourceGraph:
        raise TypeError("ResourceGraph must be an exact RoutingResourceGraph")
    if type(Technology) is not RedstoneRoutingTechnology:
        raise TypeError("Technology must be an exact RedstoneRoutingTechnology")
    if type(RawTrackAssignmentApplicable) is not bool:
        raise TypeError("RawTrackAssignmentApplicable must be an exact bool")
    if type(CandidateId) is not str or not CandidateId:
        raise ValueError("current selected-access candidate requires an id")
    if type(SourceGenerator) is not str or not SourceGenerator:
        raise ValueError("current selected-access candidate requires a source")
    if type(RoutingSpacing) is not int or RoutingSpacing < 1:
        raise ValueError("current selected-access routing spacing is invalid")
    if type(PlacementFingerprint) is not str or not PlacementFingerprint:
        raise ValueError("current selected-access placement fingerprint is invalid")
    if type(PlacementRetentionFingerprint) is not str:
        raise TypeError("placement retention fingerprint must be an exact string")
    if type(InterfaceTopologyFingerprint) is not str:
        raise TypeError("interface topology fingerprint must be an exact string")
    if type(ObservedPlacementFingerprint) is not str or not ObservedPlacementFingerprint:
        raise ValueError("observed placement fingerprint is invalid")
    if type(ObservedPlacementRetentionFingerprint) is not str:
        raise TypeError("observed placement retention fingerprint must be an exact string")

    PredecessorFingerprint = (
        Predecessor.Envelope.EnvelopeFingerprint
        if Predecessor is not None and Predecessor.Envelope is not None
        else ""
    )
    PhysicalValidation = None
    JointInputFingerprint = BuildStableFingerprint({
        "Phase": Phase.value,
        "Transition": Transition.value,
        "CandidateId": CandidateId,
        "PlacementFingerprint": PlacementFingerprint,
        "PlacementFingerprintIncludesLocalClaims": (
            PlacementFingerprintIncludesLocalClaims
        ),
    })

    def Observation(Detail: str) -> CurrentSelectedAccessPhaseObservation:
        return CurrentSelectedAccessPhaseObservation(
            Phase=Phase,
            Transition=Transition,
            CandidateId=CandidateId,
            PlacementFingerprint=PlacementFingerprint,
            PlacementFingerprintIncludesLocalClaims=(
                PlacementFingerprintIncludesLocalClaims
            ),
            PhysicalValidation=deepcopy(PhysicalValidation),
            JointInputFingerprint=JointInputFingerprint,
            PredecessorEnvelopeFingerprint=PredecessorFingerprint,
            Detail=Detail,
        )

    # Current Router channel construction deliberately clears the predecessor's
    # selected-access fields.  Classify an unrebound deck as stale transition
    # authority before the generic missing-evidence case; a fresh incomplete or
    # unsatisfiable re-solve still reaches its more specific status below.
    if (
        Transition is CurrentSelectedAccessTransition.ChannelReplacement
        and type(TransitionDeckPlacement) is PcbPlacement
        and type(Placement.PlacementAccessSolve) is not PlacementAccessSolveResult
        and not _ReboundPlacementMatchesDeck(
            TransitionDeckPlacement,
            Placement,
        )
    ):
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Stale,
            CurrentSelectedAccessEnvelopeReason.PhysicalInputMismatch,
            Observation("the channel successor has no current access rebind after its deck transform"),
            Predecessor,
        )

    SolveResult = Placement.PlacementAccessSolve
    SelectedWitness = Placement.SelectedPinAccessWitness
    if (
        Phase is CurrentSelectedAccessEnvelopePhase.BeforeRawMaterialization
        and Predecessor is not None
    ):
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Stale,
            CurrentSelectedAccessEnvelopeReason.UnexpectedPhaseDrift,
            Observation("an initial candidate cannot inherit phase authority"),
            Predecessor,
        )
    if type(SolveResult) is not PlacementAccessSolveResult:
        if not Policy.PlacementAccess.Enabled:
            return _NonReady(
                CurrentSelectedAccessEnvelopeStatus.Unavailable,
                CurrentSelectedAccessEnvelopeReason.AccessPolicyDisabled,
                Observation("placement access is disabled for this supported normal path"),
                Predecessor,
            )
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Unavailable,
            CurrentSelectedAccessEnvelopeReason.MissingSelectedAccessEvidence,
            Observation("the live candidate has no complete typed solve evidence"),
            Predecessor,
        )
    if SelectedWitness is not None and type(SelectedWitness) is not SelectedPlacementPinAccessWitness:
        raise TypeError("selected witness must be exact or None")
    if not _SolveBindingMatchesCurrent(
        SolveBinding,
        SolveResult,
        SelectedWitness,
        Policy,
    ):
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Stale,
            CurrentSelectedAccessEnvelopeReason.SolvePolicyMismatch,
            Observation("the solve producer binding does not match the complete live policy and selected evidence"),
            Predecessor,
        )
    if not Policy.PlacementAccess.Enabled:
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Unavailable,
            CurrentSelectedAccessEnvelopeReason.AccessPolicyDisabled,
            Observation("placement access is disabled for this supported normal path"),
            Predecessor,
        )
    if ResourceGraph.GraphVersion != RoutingResourceGraphVersion:
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Unavailable,
            CurrentSelectedAccessEnvelopeReason.UnsupportedResourceGraphVersion,
            Observation("the live resource graph is not routing-resource-graph-v3"),
            Predecessor,
        )
    if type(PlacementFingerprintIncludesLocalClaims) is not bool:
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Unavailable,
            CurrentSelectedAccessEnvelopeReason.PlacementFingerprintModeUnproven,
            Observation("the placement fingerprint local-claims mode is unknown"),
            Predecessor,
        )
    if type(RoutingEnvelope) is not DerivedRoutingEnvelope:
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Unavailable,
            CurrentSelectedAccessEnvelopeReason.MissingRoutingEnvelope,
            Observation("the current selected candidate has no routing envelope"),
            Predecessor,
        )
    if (
        ObservedPlacementFingerprint != PlacementFingerprint
        or ObservedPlacementRetentionFingerprint != PlacementRetentionFingerprint
    ):
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Stale,
            CurrentSelectedAccessEnvelopeReason.UnexpectedPhaseDrift,
            Observation("the declared placement fingerprint does not match the live placement"),
            Predecessor,
        )
    if Phase is not CurrentSelectedAccessEnvelopePhase.BeforeRawMaterialization:
        if Predecessor is None or (
            Predecessor.Status is not CurrentSelectedAccessEnvelopeStatus.Ready
            or Predecessor.Envelope is None
        ):
            return _NonReady(
                CurrentSelectedAccessEnvelopeStatus.Unavailable,
                CurrentSelectedAccessEnvelopeReason.MissingReadyPredecessor,
                Observation("the causal successor has no freshly Ready predecessor"),
                Predecessor,
            )
        Previous = Predecessor.Envelope
        PredecessorRelationCurrent = (
            (
                Transition
                is CurrentSelectedAccessTransition.SelectedTrackAssignment
                and Previous.Phase
                is CurrentSelectedAccessEnvelopePhase.BeforeRawMaterialization
                and Previous.Transition
                is CurrentSelectedAccessTransition.InitialCandidate
                and CandidateId == Previous.Candidate.CandidateId
            )
            or (
                Transition
                is CurrentSelectedAccessTransition.ChannelReplacement
                and Previous.Phase
                is CurrentSelectedAccessEnvelopePhase.BeforeRawMaterialization
                and Previous.Transition
                is CurrentSelectedAccessTransition.InitialCandidate
            )
            or (
                Transition
                is CurrentSelectedAccessTransition.PostRoutingCompaction
                and Previous.Phase
                is CurrentSelectedAccessEnvelopePhase.SelectedTrackSuccessor
                and Previous.Transition in {
                    CurrentSelectedAccessTransition.SelectedTrackAssignment,
                    CurrentSelectedAccessTransition.ChannelReplacement,
                }
                and CandidateId == Previous.Candidate.CandidateId
            )
        )
        if not PredecessorRelationCurrent:
            return _NonReady(
                CurrentSelectedAccessEnvelopeStatus.Stale,
                CurrentSelectedAccessEnvelopeReason.UnexpectedPhaseDrift,
                Observation(
                    "the predecessor does not match the declared causal phase"
                ),
                Predecessor,
            )

    if Transition is CurrentSelectedAccessTransition.ChannelReplacement:
        Previous = Predecessor.Envelope if Predecessor is not None else None
        TransitionRelationCurrent = (
            Previous is not None
            and type(TransitionSourcePlacement) is PcbPlacement
            and type(ChannelPlacement) is PcbPlacement
            and type(TransitionDeckPlacement) is PcbPlacement
            and type(TransitionState) is ClusterInterfacePlacementState
            and TransitionSourceCandidateId == Previous.Candidate.CandidateId
            and TransitionSourcePlacementFingerprint
            == Previous.Candidate.PlacementFingerprint
            and TransitionSourcePlacementRetentionFingerprint
            == Previous.Candidate.PlacementRetentionFingerprint
            and TransitionSourcePlacementFingerprintIncludesLocalClaims
            == Previous.Candidate.PlacementFingerprintIncludesLocalClaims
            and _PlacementTransitionBasePayload(TransitionSourcePlacement)
            == Previous.Candidate.PlacementTransitionBasePayload
            and _ChannelPlacementMatchesSource(
                TransitionSourcePlacement,
                ChannelPlacement,
            )
            and _DeckPlacementMatchesChannel(
                ChannelPlacement,
                TransitionDeckPlacement,
                TransitionState,
                PlacementFingerprint=PlacementFingerprint,
                InterfaceTopologyFingerprint=InterfaceTopologyFingerprint,
            )
            and _ReboundPlacementMatchesDeck(
                TransitionDeckPlacement,
                Placement,
            )
        )
        if not TransitionRelationCurrent:
            return _NonReady(
                CurrentSelectedAccessEnvelopeStatus.Stale,
                CurrentSelectedAccessEnvelopeReason.PlacementTransitionMismatch,
                Observation("the channel successor is unrelated to its owned predecessor"),
                Predecessor,
            )
    elif any(Value is not None for Value in (
        TransitionSourcePlacement,
        ChannelPlacement,
        TransitionDeckPlacement,
        TransitionState,
    )) or any(Value not in ("", None) for Value in (
        TransitionSourceCandidateId,
        TransitionSourcePlacementFingerprint,
        TransitionSourcePlacementRetentionFingerprint,
        TransitionSourcePlacementFingerprintIncludesLocalClaims,
    )):
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Stale,
            CurrentSelectedAccessEnvelopeReason.PlacementTransitionMismatch,
            Observation("unchanged phase received unowned transition inputs"),
            Predecessor,
        )

    BeforeJointPayload = {
        "Policy": Policy,
        "Placement": _PlacementPayload(Placement),
        "RoutingEnvelope": RoutingEnvelope,
        "TrackPreparation": TrackPreparation,
        "RawTrackAssignment": RawTrackAssignment,
        "SolveBinding": SolveBinding,
        "TransitionSourcePlacement": (
            _PlacementPayload(TransitionSourcePlacement)
            if TransitionSourcePlacement is not None
            else None
        ),
        "ChannelPlacement": (
            _PlacementPayload(ChannelPlacement)
            if ChannelPlacement is not None
            else None
        ),
        "TransitionDeckPlacement": (
            _PlacementPayload(TransitionDeckPlacement)
            if TransitionDeckPlacement is not None
            else None
        ),
        "TransitionState": TransitionState,
    }
    BeforeJointFingerprint = BuildStableFingerprint(_AuthorityDictionary(
        _FreezeAuthorityValue(BeforeJointPayload, Path="JointInputs")
    ))
    PhysicalValidation = ValidateCurrentSelectedPlacementAccess(
        Placement.Placed.PlacedGates,
        SelectedWitness,
        SolveResult,
        ResourceGraph=ResourceGraph,
        Technology=Technology,
        FrozenNetWires=Placement.Placed.FrozenNetWires or {},
    )
    AfterJointPayload = {
        "Policy": Policy,
        "Placement": _PlacementPayload(Placement),
        "RoutingEnvelope": RoutingEnvelope,
        "TrackPreparation": TrackPreparation,
        "RawTrackAssignment": RawTrackAssignment,
        "SolveBinding": SolveBinding,
        "TransitionSourcePlacement": (
            _PlacementPayload(TransitionSourcePlacement)
            if TransitionSourcePlacement is not None
            else None
        ),
        "ChannelPlacement": (
            _PlacementPayload(ChannelPlacement)
            if ChannelPlacement is not None
            else None
        ),
        "TransitionDeckPlacement": (
            _PlacementPayload(TransitionDeckPlacement)
            if TransitionDeckPlacement is not None
            else None
        ),
        "TransitionState": TransitionState,
    }
    AfterJointFingerprint = BuildStableFingerprint(_AuthorityDictionary(
        _FreezeAuthorityValue(AfterJointPayload, Path="JointInputs")
    ))
    JointInputFingerprint = AfterJointFingerprint
    if BeforeJointFingerprint != AfterJointFingerprint:
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Stale,
            CurrentSelectedAccessEnvelopeReason.CurrentInputChangedDuringCapture,
            Observation("declared Joint inputs changed during fresh capture"),
            Predecessor,
        )
    if (
        PhysicalValidation.Status
        is CurrentSelectedPlacementAccessValidationStatus.Mismatch
    ):
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Stale,
            CurrentSelectedAccessEnvelopeReason.PhysicalInputMismatch,
            Observation("Physical current validation found a mismatch"),
            Predecessor,
        )
    if (
        type(SolveResult.PolicyVersion) is not str
        or not SolveResult.PolicyVersion
        or SolveResult.PolicyVersion != Policy.PolicyVersion
    ):
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Stale,
            CurrentSelectedAccessEnvelopeReason.SolvePolicyMismatch,
            Observation("the selected access solve does not name the live policy"),
            Predecessor,
        )
    if (
        PhysicalValidation.Status
        is not CurrentSelectedPlacementAccessValidationStatus.Verified
        or PhysicalValidation.Reason
        is not CurrentSelectedPlacementAccessValidationReason.Current
    ):
        if (
            PhysicalValidation.Reason
            is CurrentSelectedPlacementAccessValidationReason.IncompleteSolve
        ):
            return _NonReady(
                CurrentSelectedAccessEnvelopeStatus.Incomplete,
                CurrentSelectedAccessEnvelopeReason.AccessSolveIncomplete,
                Observation("the current access solve is bounded and incomplete"),
                Predecessor,
            )
        if (
            PhysicalValidation.Reason
            is CurrentSelectedPlacementAccessValidationReason.UnsatisfiableSolve
        ):
            return _NonReady(
                CurrentSelectedAccessEnvelopeStatus.Unsatisfiable,
                CurrentSelectedAccessEnvelopeReason.AccessSolveUnsatisfiable,
                Observation(
                    "the current access solve is complete and unsatisfiable"
                ),
                Predecessor,
            )
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Incomplete,
            CurrentSelectedAccessEnvelopeReason.PhysicalInputUnresolved,
            Observation("Physical current validation did not resolve Verified/Current"),
            Predecessor,
        )
    if Transition is CurrentSelectedAccessTransition.ChannelReplacement and (
        Placement.SelectedPinAccessWitness
        != Placement.Placed.SelectedPinAccessWitness
        or Placement.PlacementAccessSolve
        != Placement.Placed.PlacementAccessSolve
    ):
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Stale,
            CurrentSelectedAccessEnvelopeReason.PlacementTransitionMismatch,
            Observation(
                "the rebound placement does not attach the same access evidence to its placed design"
            ),
            Predecessor,
        )

    if Phase is not CurrentSelectedAccessEnvelopePhase.BeforeRawMaterialization:
        if type(TrackPreparation) is not TrackAssignmentPreparation:
            return _NonReady(
                CurrentSelectedAccessEnvelopeStatus.Incomplete,
                CurrentSelectedAccessEnvelopeReason.MissingTrackPreparation,
                Observation("the causal successor has no selected track preparation"),
                Predecessor,
            )
        if not TrackPreparation.Success or not TrackPreparation.Complete:
            return _NonReady(
                CurrentSelectedAccessEnvelopeStatus.Incomplete,
                CurrentSelectedAccessEnvelopeReason.IncompleteTrackPreparation,
                Observation("the selected track preparation is not complete"),
                Predecessor,
            )
        if (
            SelectedWitness is None
            or TrackPreparation.PinAccessDomainFingerprint
            != SelectedWitness.DomainFingerprint
            or TrackPreparation.PinAccessWitnessFingerprint
            != SelectedWitness.WitnessFingerprint
        ):
            return _NonReady(
                CurrentSelectedAccessEnvelopeStatus.Stale,
                CurrentSelectedAccessEnvelopeReason.TrackPreparationMismatch,
                Observation("the selected track preparation is not bound to the current access witness"),
                Predecessor,
            )
        if RawTrackAssignmentApplicable and RawTrackAssignment is None:
            return _NonReady(
                CurrentSelectedAccessEnvelopeStatus.Incomplete,
                CurrentSelectedAccessEnvelopeReason.MissingRawTrackAssignment,
                Observation("the applicable raw track assignment is missing"),
                Predecessor,
            )
        if RawTrackAssignmentApplicable and type(RawTrackAssignment) is not RawTrackAssignmentSelection:
            raise TypeError("raw track assignment must be an exact selection")
        if RawTrackAssignmentApplicable and (
            not RawTrackAssignment.Success
            or not RawTrackAssignment.Complete
        ):
            return _NonReady(
                CurrentSelectedAccessEnvelopeStatus.Incomplete,
                CurrentSelectedAccessEnvelopeReason.IncompleteRawTrackAssignment,
                Observation("the applicable raw track assignment is incomplete"),
                Predecessor,
            )

    OwnedSolve = deepcopy(SolveResult)
    OwnedSolveBinding = deepcopy(SolveBinding)
    OwnedWitness = deepcopy(SelectedWitness)
    OwnedDomains = deepcopy(tuple(SolveResult.Domains))
    OwnedPolicy = _PolicySnapshot(Policy)
    PlacementPayload = _PlacementPayload(deepcopy(Placement))
    Candidate = CurrentSelectedAccessCandidateSnapshot(
        CandidateId=CandidateId,
        SourceGenerator=SourceGenerator,
        RoutingSpacing=RoutingSpacing,
        PlacementFingerprint=PlacementFingerprint,
        PlacementRetentionFingerprint=PlacementRetentionFingerprint,
        ObservedPlacementFingerprint=ObservedPlacementFingerprint,
        ObservedPlacementRetentionFingerprint=(
            ObservedPlacementRetentionFingerprint
        ),
        PlacementFingerprintIncludesLocalClaims=(
            PlacementFingerprintIncludesLocalClaims
        ),
        PlacementTransitionBasePayload=(
            _PlacementTransitionBasePayload(deepcopy(Placement))
        ),
        PlacementCorePayload=_PlacementCorePayload(deepcopy(Placement)),
        PlacementPayload=PlacementPayload,
    )
    OwnedRoutingEnvelope = deepcopy(RoutingEnvelope)
    RoutingEnvelopePayload = _FreezeAuthorityValue(
        OwnedRoutingEnvelope.ToDictionary(), Path="RoutingEnvelope"
    )
    if type(RoutingEnvelopePayload) is not tuple:
        raise TypeError("routing envelope authority payload is invalid")
    OwnedTrackPreparation = deepcopy(TrackPreparation)
    TrackPreparationPayload = (
        _FreezeAuthorityValue(
            OwnedTrackPreparation.ToDictionary(), Path="TrackPreparation"
        )
        if OwnedTrackPreparation is not None
        else None
    )
    if TrackPreparationPayload is not None and type(TrackPreparationPayload) is not tuple:
        raise TypeError("track preparation authority payload is invalid")
    TrackPreparationAuthorityPayload = (
        _TrackPreparationAuthority(OwnedTrackPreparation)
        if OwnedTrackPreparation is not None
        else None
    )
    RawTrackAssignmentAuthorityPayload = (
        _RawTrackAssignmentAuthority(
            RawTrackAssignment,
            OwnedTrackPreparation,
            CandidateId=CandidateId,
        )
        if RawTrackAssignmentApplicable
        else None
    )
    if RawTrackAssignmentApplicable and RawTrackAssignmentAuthorityPayload is None:
        return _NonReady(
            CurrentSelectedAccessEnvelopeStatus.Stale,
            CurrentSelectedAccessEnvelopeReason.RawTrackAssignmentMismatch,
            Observation("the raw selection is not bound to the selected track preparation"),
            Predecessor,
        )
    RawTrackAssignmentPayload = (
        _FreezeAuthorityValue(
            deepcopy(RawTrackAssignment).ToDictionary(),
            Path="RawTrackAssignment",
        )
        if RawTrackAssignment is not None
        else None
    )
    if RawTrackAssignmentPayload is not None and type(RawTrackAssignmentPayload) is not tuple:
        raise TypeError("raw track assignment authority payload is invalid")
    RequiredFields = (
        "PhysicalValidation",
        "SolveResult",
        "SelectedWitness",
        "Domains",
        "Candidate",
        "Policy",
        "RoutingEnvelope",
        "InclusiveRoutingBoundsXZ",
        "LogicalRoutingLayers",
        "PlacementFingerprintIncludesLocalClaims",
        *(
            ("TrackPreparation", "SelectedTrackAssignmentFacts")
            if Phase is not CurrentSelectedAccessEnvelopePhase.BeforeRawMaterialization
            else ()
        ),
        *(
            ("RawTrackAssignment",)
            if RawTrackAssignmentApplicable
            and Phase is not CurrentSelectedAccessEnvelopePhase.BeforeRawMaterialization
            else ()
        ),
    )
    OutOfScopeFields = (
        ("TrackPreparation", "RawTrackAssignment", "RoutedCompaction")
        if Phase is CurrentSelectedAccessEnvelopePhase.BeforeRawMaterialization
        else (
            ("RawTrackAssignment", "RoutedCompaction")
            if not RawTrackAssignmentApplicable
            else ("RoutedCompaction",)
        )
        if Phase is CurrentSelectedAccessEnvelopePhase.SelectedTrackSuccessor
        else (("RawTrackAssignment",) if not RawTrackAssignmentApplicable else ())
    )
    Routing = CurrentSelectedAccessRoutingSnapshot(
        RoutingEnvelope=OwnedRoutingEnvelope,
        RoutingEnvelopePayload=RoutingEnvelopePayload,
        InclusiveRoutingBoundsXZ=tuple(OwnedRoutingEnvelope.EnvelopeBounds),
        LogicalRoutingLayers=tuple(OwnedRoutingEnvelope.PermittedLayers),
        TrackPreparation=OwnedTrackPreparation,
        TrackPreparationPayload=TrackPreparationPayload,
        TrackPreparationAuthorityPayload=TrackPreparationAuthorityPayload,
        RawTrackAssignmentApplicable=RawTrackAssignmentApplicable,
        RawTrackAssignment=(
            deepcopy(RawTrackAssignment)
            if RawTrackAssignmentApplicable
            else None
        ),
        RawTrackAssignmentPayload=RawTrackAssignmentPayload,
        RawTrackAssignmentAuthorityFingerprint=(
            BuildStableFingerprint(
                _AuthorityDictionary(RawTrackAssignmentAuthorityPayload)
            )
            if RawTrackAssignmentAuthorityPayload is not None
            else ""
        ),
        RequiredFields=RequiredFields,
        UnavailableFields=(),
        OutOfScopeFields=OutOfScopeFields,
    )
    TransitionSourcePlacementPayload = None
    ChannelPlacementPayload = None
    TransitionDeckPlacementPayload = None
    OwnedTransitionState = None
    if Transition is CurrentSelectedAccessTransition.ChannelReplacement:
        TransitionSourcePlacementPayload = _PlacementPayload(
            deepcopy(TransitionSourcePlacement)
        )
        ChannelPlacementPayload = _PlacementPayload(deepcopy(ChannelPlacement))
        TransitionDeckPlacementPayload = _PlacementPayload(
            deepcopy(TransitionDeckPlacement)
        )
        OwnedTransitionState = deepcopy(TransitionState)

    Envelope = CurrentSelectedAccessEnvelope(
        Phase=Phase,
        Transition=Transition,
        PhysicalValidation=deepcopy(PhysicalValidation),
        SolveBinding=OwnedSolveBinding,
        SolveResult=OwnedSolve,
        SelectedWitness=OwnedWitness,
        Domains=OwnedDomains,
        Policy=OwnedPolicy,
        Candidate=Candidate,
        Routing=Routing,
        TransitionSourcePlacementPayload=TransitionSourcePlacementPayload,
        ChannelPlacementPayload=ChannelPlacementPayload,
        TransitionDeckPlacementPayload=TransitionDeckPlacementPayload,
        TransitionState=OwnedTransitionState,
        PredecessorEnvelopeFingerprint=PredecessorFingerprint,
    )

    if Predecessor is not None and Predecessor.Envelope is not None:
        Previous = Predecessor.Envelope
        SameSelectedEvidence = (
            Previous.SelectedAccessEvidenceFingerprint
            == Envelope.SelectedAccessEvidenceFingerprint
        )
        SamePlacement = (
            Previous.Candidate.PlacementFingerprint
            == Envelope.Candidate.PlacementFingerprint
            and Previous.Candidate.PlacementCorePayload
            == Envelope.Candidate.PlacementCorePayload
            and Previous.Candidate.PlacementFingerprintIncludesLocalClaims
            == Envelope.Candidate.PlacementFingerprintIncludesLocalClaims
        )
        AllowsPlacementSuccessor = (
            Transition is CurrentSelectedAccessTransition.ChannelReplacement
            and TransitionSourcePlacementPayload is not None
            and ChannelPlacementPayload is not None
            and TransitionDeckPlacementPayload is not None
            and OwnedTransitionState is not None
        )
        SameSelectedTrackFacts = (
            Previous.Routing.TrackPreparationAuthorityPayload is None
            or Previous.Routing.TrackPreparationAuthorityPayload
            == Envelope.Routing.TrackPreparationAuthorityPayload
        )
        SameRawTrackFacts = (
            not Previous.Routing.RawTrackAssignmentAuthorityFingerprint
            or Previous.Routing.RawTrackAssignmentAuthorityFingerprint
            == Envelope.Routing.RawTrackAssignmentAuthorityFingerprint
        )
        if (
            (not SameSelectedEvidence and not AllowsPlacementSuccessor)
            or (not SameSelectedTrackFacts and not AllowsPlacementSuccessor)
            or (not SameRawTrackFacts and not AllowsPlacementSuccessor)
            or (not SamePlacement and not AllowsPlacementSuccessor)
        ):
            return _NonReady(
                CurrentSelectedAccessEnvelopeStatus.Stale,
                CurrentSelectedAccessEnvelopeReason.UnexpectedPhaseDrift,
                Observation("the phase changed inputs outside its declared transition"),
                Predecessor,
            )

    CurrentObservation = CurrentSelectedAccessPhaseObservation(
        Phase=Phase,
        Transition=Transition,
        CandidateId=CandidateId,
        PlacementFingerprint=PlacementFingerprint,
        PlacementFingerprintIncludesLocalClaims=(
            PlacementFingerprintIncludesLocalClaims
        ),
        PhysicalValidation=deepcopy(PhysicalValidation),
        JointInputFingerprint=Envelope.CurrentPhaseInputFingerprint,
        PredecessorEnvelopeFingerprint=PredecessorFingerprint,
        Detail="fresh Physical and Joint inputs are current for this phase",
    )
    History = Predecessor.Observations if Predecessor is not None else ()
    return CurrentSelectedAccessEnvelopeResult(
        Status=CurrentSelectedAccessEnvelopeStatus.Ready,
        Reason=CurrentSelectedAccessEnvelopeReason.Current,
        Observations=(*History, CurrentObservation),
        Envelope=Envelope,
    )


def RequireCurrentSelectedAccessEnvelopeReady(
    Result: CurrentSelectedAccessEnvelopeResult,
    *,
    Stage: str,
) -> CurrentSelectedAccessEnvelope:
    """Require a fresh Ready result at a public orchestration boundary."""
    if type(Result) is not CurrentSelectedAccessEnvelopeResult:
        raise TypeError("current selected-access gate requires a typed result")
    if Result.Status is CurrentSelectedAccessEnvelopeStatus.Ready:
        if Result.Envelope is None:
            raise ValueError("Ready current selected-access result lost its envelope")
        return Result.Envelope
    FailureReason = (
        RoutingFailureReason.ClusterInterfaceSolveIncomplete
        if Result.Status is CurrentSelectedAccessEnvelopeStatus.Incomplete
        else RoutingFailureReason.NoPinAccessPattern
        if Result.Status is CurrentSelectedAccessEnvelopeStatus.Unsatisfiable
        else RoutingFailureReason.ClusterInterfaceInvariantViolation
    )
    raise RoutingStageError(RoutingFailure(
        Reason=FailureReason,
        Stage=Stage,
        Detail="the current selected-access envelope is not Ready",
        Diagnostics={
            "CurrentSelectedAccessEnvelope": Result.ToDictionary(),
            "CurrentSelectedAccessStatus": Result.Status.value,
            "CurrentSelectedAccessReason": Result.Reason.value,
        },
    ))


__all__ = [
    "BuildCurrentSelectedAccessEnvelope",
    "CurrentSelectedAccessEnvelope",
    "CurrentSelectedAccessEnvelopePhase",
    "CurrentSelectedAccessEnvelopeReason",
    "CurrentSelectedAccessEnvelopeResult",
    "CurrentSelectedAccessEnvelopeStatus",
    "CurrentSelectedAccessTransition",
    "RequireCurrentSelectedAccessEnvelopeReady",
]
