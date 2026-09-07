"""Immutable observations and pure validation of the Stage-1 access handoff."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .Failures import RoutingFailure, RoutingFailureReason, RoutingStageError
from .PlacementAccess import PlacementAccessSolveResult, PlacementAccessSolveStatus, SelectedPlacementPinAccessWitness
from ..Runtime.Reliability import BuildStableFingerprint


PlacementPinAccessStages = (
    "Placement", "AccessFabric", "RawAssignment", "DetailedRouting", "Compaction",
)


@dataclass(frozen=True)
class PlacementPinAccessStageObservation:
    Stage: str
    PolicyVersion: str
    CatalogVersion: str
    TechnologyFingerprint: str
    ResourceModelFingerprint: str
    DomainFingerprint: str
    WitnessFingerprint: str
    AccessRegenerationCount: int = 0
    UnselectedPortalLeakCount: int = 0
    CompactionPreserved: bool | None = None

    def ToDictionary(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def FromWitness(cls, Stage: str, Witness: SelectedPlacementPinAccessWitness, PolicyVersion: str) -> PlacementPinAccessStageObservation:
        return cls(
            Stage, PolicyVersion, Witness.CatalogVersion,
            Witness.TechnologyFingerprint, Witness.ResourceModelFingerprint,
            Witness.DomainFingerprint, Witness.WitnessFingerprint,
        )


@dataclass(frozen=True)
class PlacementPinAccessHandoffEvidence:
    Observations: tuple[PlacementPinAccessStageObservation, ...]
    SolveResultFingerprint: str
    SchemaVersion: str = "placement-pin-access-handoff-v1"

    def ToDictionary(self) -> dict[str, object]:
        Payload = {
            "SchemaVersion": self.SchemaVersion,
            "Observations": [Observation.ToDictionary() for Observation in self.Observations],
            "SolveResultFingerprint": self.SolveResultFingerprint,
        }
        return {**Payload, "EvidenceFingerprint": BuildStableFingerprint(Payload)}


def ValidatePlacementPinAccessHandoff(
    ExpectedWitness: SelectedPlacementPinAccessWitness,
    OrderedStageObservations: tuple[PlacementPinAccessStageObservation, ...],
    *,
    SolveResult: PlacementAccessSolveResult,
    PolicyVersion: str,
    CatalogVersion: str,
    TechnologyFingerprint: str,
    ResourceModelFingerprint: str,
) -> PlacementPinAccessHandoffEvidence:
    """Check current identity and evidence; never repair or regenerate geometry."""
    if not isinstance(ExpectedWitness, SelectedPlacementPinAccessWitness) or not isinstance(SolveResult, PlacementAccessSolveResult) or not isinstance(OrderedStageObservations, tuple) or not all(isinstance(Value, PlacementPinAccessStageObservation) for Value in OrderedStageObservations):
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ClusterInterfaceInvariantViolation,
            Stage="PlacementPinAccessFinalization",
            Detail="placement pin-access handoff requires immutable typed records",
        ))
    Errors = []
    if not isinstance(OrderedStageObservations, tuple) or tuple(Value.Stage for Value in OrderedStageObservations) != PlacementPinAccessStages:
        Errors.append("missing, repeated, or reordered handoff stages")
    if not isinstance(SolveResult, PlacementAccessSolveResult) or SolveResult.Status is not PlacementAccessSolveStatus.Feasible or SolveResult.SelectedWitness != ExpectedWitness:
        Errors.append("witness was not selected by the supplied feasible solve")
    if not ExpectedWitness.Complete or not ExpectedWitness.Domains:
        Errors.append("witness has no complete domain evidence")
    ExpectedIdentity = (PolicyVersion, CatalogVersion, TechnologyFingerprint, ResourceModelFingerprint, ExpectedWitness.DomainFingerprint, ExpectedWitness.WitnessFingerprint)
    if not all(type(Value) is str and Value for Value in ExpectedIdentity):
        Errors.append("current handoff identity is incomplete")
    if (ExpectedWitness.CatalogVersion, ExpectedWitness.TechnologyFingerprint, ExpectedWitness.ResourceModelFingerprint) != ExpectedIdentity[1:4]:
        Errors.append("witness is stale against current policy or physical model")
    if SolveResult.PolicyVersion != PolicyVersion:
        Errors.append("solve policy is stale")
    for Observation in OrderedStageObservations:
        if (Observation.PolicyVersion, Observation.CatalogVersion, Observation.TechnologyFingerprint, Observation.ResourceModelFingerprint, Observation.DomainFingerprint, Observation.WitnessFingerprint) != ExpectedIdentity:
            Errors.append(f"{Observation.Stage}: identity mismatch")
        if type(Observation.AccessRegenerationCount) is not int or Observation.AccessRegenerationCount != 0 or type(Observation.UnselectedPortalLeakCount) is not int or Observation.UnselectedPortalLeakCount != 0:
            Errors.append(f"{Observation.Stage}: missing or nonzero regeneration/leak count")
        if Observation.Stage == "Compaction":
            if Observation.CompactionPreserved is not True:
                Errors.append("Compaction: selected geometry was not preserved")
        elif Observation.CompactionPreserved is not None:
            Errors.append(f"{Observation.Stage}: unexpected compaction claim")
    if Errors:
        raise RoutingStageError(RoutingFailure(
            Reason=RoutingFailureReason.ClusterInterfaceInvariantViolation,
            Stage="PlacementPinAccessFinalization",
            Detail="the selected pin-access contract changed after placement",
            Diagnostics={"Errors": Errors, "Observations": [Value.ToDictionary() for Value in OrderedStageObservations]},
        ))
    return PlacementPinAccessHandoffEvidence(OrderedStageObservations, SolveResult.ToDictionary()["ResultFingerprint"])
