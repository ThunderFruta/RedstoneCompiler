"""Ordered, independently testable authoritative routing phases."""

from .Bootstrap import RunBootstrap
from .GuidePlanning import RunGuidePlanning
from .PortalPreparation import RunPortalPreparation
from .CandidatePreparation import RunCandidatePreparation
from .CandidateMaterialization import RunCandidateMaterialization
from .AssignmentPreparation import RunAssignmentPreparation
from .AssignmentSolve import RunAssignmentSolve
from .Materialization import RunMaterialization

AUTHORITATIVE_ROUTING_PHASES = (
    RunBootstrap,
    RunGuidePlanning,
    RunPortalPreparation,
    RunCandidatePreparation,
    RunCandidateMaterialization,
    RunAssignmentPreparation,
    RunAssignmentSolve,
    RunMaterialization,
)

__all__ = (
    "AUTHORITATIVE_ROUTING_PHASES",
    "RunBootstrap",
    "RunGuidePlanning",
    "RunPortalPreparation",
    "RunCandidatePreparation",
    "RunCandidateMaterialization",
    "RunAssignmentPreparation",
    "RunAssignmentSolve",
    "RunMaterialization",
)
