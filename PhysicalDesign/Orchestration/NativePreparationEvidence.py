"""Bind native preparation observations to the coordinator's candidate inputs."""

from __future__ import annotations

import json
from PhysicalDesign.Constraints.BoundaryRelations import (
    BuildRawPortalPlacementGeometryFingerprint,
    BuildRawPortalResourceGeometryFingerprint,
)

from PhysicalDesign.Routing.Global.NativePreparationEvidence import (
    BindCandidateNativePreparationEvidence,
    CombineNativePreparationEvidence,
    UnavailableNativePreparationEvidence,
)


def RetainCandidateNativePreparationEvidence(
    Context,
    Candidate,
    Observation,
    *,
    CandidateInputFingerprint=None,
    Domain=None,
    Preparation=None,
    Resources=None,
):
    """Retain owned observations without changing candidate/result authority."""
    try:
        Source = Domain if Domain is not None else Preparation
        Witness = Candidate.Placement.SelectedPinAccessWitness
        if Resources is None:
            Resources = Context.RoutingResourcesByCandidateId.get(Candidate.CandidateId)
        PlacementFingerprint = BuildRawPortalPlacementGeometryFingerprint(Candidate.Placement.Placed)
        ResourceFingerprint = BuildRawPortalResourceGeometryFingerprint(Resources) if Resources is not None else ""
        if Domain is not None and (
            Domain.PlacementFingerprint != PlacementFingerprint
            or (ResourceFingerprint and Domain.ResourceGraphFingerprint != ResourceFingerprint)
        ):
            raise ValueError("raw preparation/current candidate geometry scope mismatch")
        SourceDiagnostics = dict(getattr(Source, "Diagnostics", ()))
        Scope = {
            "PlacementFingerprint": PlacementFingerprint,
            "ResourceGraphFingerprint": ResourceFingerprint or getattr(Source, "ResourceGraphFingerprint", ""),
            "PortalDomainFingerprint": getattr(Source, "PortalDomainFingerprint", ""),
            "CandidateDomainFingerprint": getattr(Source, "CandidateDomainFingerprint", ""),
            "LocalClaimDomainFingerprint": getattr(Source, "LocalClaimDomainFingerprint", ""),
            "PinAccessDomainFingerprint": (
                getattr(Witness, "DomainFingerprint", "")
                or getattr(Source, "PinAccessDomainFingerprint", "")
            ),
            "PinAccessWitnessFingerprint": (
                getattr(Witness, "WitnessFingerprint", "")
                or getattr(Source, "PinAccessWitnessFingerprint", "")
                or SourceDiagnostics.get("PlacementPinAccessWitnessFingerprint", "")
            ),
        }
        Encoded = BindCandidateNativePreparationEvidence(
            Observation,
            CandidateId=Candidate.CandidateId,
            CandidateInputFingerprint=CandidateInputFingerprint,
            Scope=Scope,
            SemanticDomainComplete=getattr(Source, "Complete", None),
        )
        Context.NativePreparationObservationsByCandidateId[Candidate.CandidateId] = Encoded
        return Encoded
    except Exception as Error:
        Encoded = UnavailableNativePreparationEvidence(
            f"candidate preparation binding failed: {type(Error).__name__}",
        )
        try:
            Context.NativePreparationObservationsByCandidateId[Candidate.CandidateId] = Encoded
        except Exception:
            pass
        return Encoded


def ProjectCoordinatorNativePreparationEvidence(Context):
    """Keep absent historical transport distinct from observed empty work."""
    try:
        return CombineNativePreparationEvidence(
            getattr(Context, "NativePreparationObservationsByCandidateId", {}),
        )
    except Exception as Error:
        return json.loads(UnavailableNativePreparationEvidence(
            f"coordinator preparation projection failed: {type(Error).__name__}",
        ))
