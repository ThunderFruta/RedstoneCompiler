"""Isolated preparation workers for immutable component eligibility domains.

The parent owns every routing cache and every proof/cut mutation.  A worker is
therefore deliberately limited to building a fresh resource graph and
returning the frozen factor domain that was derived from it.  The parent
revalidates the domain against its own equivalent graph before it is solved.
"""

from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import os
from time import monotonic
from typing import Any, Iterable

from ...Redstone.Rules.Geometry import BuildRoutingResources
from ...Contracts.Failures import RoutingStageError
from ..Pcb import PreparePhysicalComponentEligibility
from ...Runtime.Reliability import RoutingDeadline


@dataclass(frozen=True)
class ImmutableEligibilityPreparationRequest:
    """One frozen queue item that may be prepared outside the parent."""

    ComponentVariant: int
    QueueOrder: int
    PlacementFingerprint: str
    CutEpoch: int
    Placement: Any
    Policy: Any
    StateFingerprint: str
    LocalRouteFingerprint: str
    DeadlineAt: float


@dataclass(frozen=True)
class ImmutableEligibilityPreparationOutcome:
    """A typed worker result with no mutable resource/cache state."""

    ComponentVariant: int
    QueueOrder: int
    PlacementFingerprint: str
    CutEpoch: int
    Status: str
    Preparation: Any = None
    Failure: Any = None
    Diagnostics: tuple[tuple[str, Any], ...] = ()
    ElapsedSeconds: float = 0.0

    @property
    def CanonicalKey(self) -> tuple[int, int, str]:
        return (
            self.ComponentVariant,
            self.QueueOrder,
            self.PlacementFingerprint,
        )


def PrepareImmutableEligibilityWorker(
    Request: ImmutableEligibilityPreparationRequest,
) -> ImmutableEligibilityPreparationOutcome:
    """Prepare one state with one native worker and no shared parent state."""

    # A batch has up to six Python processes.  Never multiply that by a Rayon
    # pool in each process: the parent restores its requested thread count
    # after the batch and uses it for native work outside this boundary.
    os.environ["RC_ROUTING_THREADS"] = "1"
    StartedAt = monotonic()
    Deadline = RoutingDeadline(
        StartedAt=StartedAt,
        ExpiresAt=Request.DeadlineAt,
        ExpirationKind="StageReserveExpired",
    )
    if Deadline.IsExpired():
        return ImmutableEligibilityPreparationOutcome(
            ComponentVariant=Request.ComponentVariant,
            QueueOrder=Request.QueueOrder,
            PlacementFingerprint=Request.PlacementFingerprint,
            CutEpoch=Request.CutEpoch,
            Status="incomplete",
            Diagnostics=(("Reason", "deadline-expired-before-worker-start"),),
            ElapsedSeconds=monotonic() - StartedAt,
        )
    try:
        Resources = BuildRoutingResources(
            Request.Placement.Placed,
            WorkCheck=lambda Diagnostics: Deadline.RaiseIfExpired(
                "ImmutableEligibilityResourceMaterialization",
                Diagnostics,
            ),
        )
        Preparation = PreparePhysicalComponentEligibility(
            Request.Placement,
            Resources=Resources,
            Policy=Request.Policy,
            Deadline=Deadline,
            StateFingerprint=Request.StateFingerprint,
            LocalRouteFingerprint=Request.LocalRouteFingerprint,
        )
        Status = (
            "prepared"
            if Preparation.Complete and Preparation.Feasible
            else "unsatisfiable"
            if Preparation.Complete
            else "incomplete"
        )
        return ImmutableEligibilityPreparationOutcome(
            ComponentVariant=Request.ComponentVariant,
            QueueOrder=Request.QueueOrder,
            PlacementFingerprint=Request.PlacementFingerprint,
            CutEpoch=Request.CutEpoch,
            Status=Status,
            Preparation=Preparation,
            Diagnostics=(
                ("NativeRoutingThreads", 1),
                ("DomainFingerprint", str(Preparation.DomainFingerprint)),
                ("Complete", bool(Preparation.Complete)),
                ("Feasible", bool(Preparation.Feasible)),
            ),
            ElapsedSeconds=monotonic() - StartedAt,
        )
    except RoutingStageError as Error:
        return ImmutableEligibilityPreparationOutcome(
            ComponentVariant=Request.ComponentVariant,
            QueueOrder=Request.QueueOrder,
            PlacementFingerprint=Request.PlacementFingerprint,
            CutEpoch=Request.CutEpoch,
            Status="incomplete" if Deadline.IsExpired() else "unsatisfiable",
            Failure=Error.Failure,
            Diagnostics=(("Stage", Error.Failure.Stage),),
            ElapsedSeconds=monotonic() - StartedAt,
        )
    except Exception as Error:
        # A worker exception has no exhaustive proof.  It is always an
        # incomplete result and must never be interpreted as UNSAT.
        return ImmutableEligibilityPreparationOutcome(
            ComponentVariant=Request.ComponentVariant,
            QueueOrder=Request.QueueOrder,
            PlacementFingerprint=Request.PlacementFingerprint,
            CutEpoch=Request.CutEpoch,
            Status="incomplete",
            Diagnostics=(("Exception", repr(Error)),),
            ElapsedSeconds=monotonic() - StartedAt,
        )


def MergeImmutableEligibilityPreparationOutcomes(
    Outcomes: Iterable[ImmutableEligibilityPreparationOutcome],
    *,
    CurrentCutEpoch: int,
    DeadlineAt: float,
) -> tuple[tuple[ImmutableEligibilityPreparationOutcome, ...], int]:
    """Discard stale work and return current outcomes in canonical order."""

    Collected = tuple(Outcomes)
    Current = tuple(sorted(
        (
            Outcome
            for Outcome in Collected
            if Outcome.CutEpoch == CurrentCutEpoch
            and monotonic() < DeadlineAt
        ),
        key=lambda Outcome: Outcome.CanonicalKey,
    ))
    return Current, len(Collected) - len(Current)


def PrepareImmutableEligibilityBatch(
    Requests: Iterable[ImmutableEligibilityPreparationRequest],
    *,
    MaximumWorkers: int = 6,
) -> tuple[ImmutableEligibilityPreparationOutcome, ...]:
    """Execute no more than six frozen preparations and merge by queue order."""

    OrderedRequests = tuple(sorted(
        Requests,
        key=lambda Request: (
            Request.ComponentVariant,
            Request.QueueOrder,
            Request.PlacementFingerprint,
        ),
    ))
    if not OrderedRequests:
        return ()
    WorkerCount = min(6, max(1, int(MaximumWorkers)), len(OrderedRequests))
    if WorkerCount == 1:
        return tuple(PrepareImmutableEligibilityWorker(Request)
                     for Request in OrderedRequests)
    Context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=WorkerCount,
        mp_context=Context,
    ) as Executor:
        Futures = {
            (
                Request.ComponentVariant,
                Request.QueueOrder,
                Request.PlacementFingerprint,
            ): Executor.submit(PrepareImmutableEligibilityWorker, Request)
            for Request in OrderedRequests
        }
        Outcomes = []
        for Request in OrderedRequests:
            Key = (
                Request.ComponentVariant,
                Request.QueueOrder,
                Request.PlacementFingerprint,
            )
            Remaining = max(0.0, Request.DeadlineAt - monotonic())
            if Remaining <= 0.0:
                Outcomes.append(ImmutableEligibilityPreparationOutcome(
                    ComponentVariant=Request.ComponentVariant,
                    QueueOrder=Request.QueueOrder,
                    PlacementFingerprint=Request.PlacementFingerprint,
                    CutEpoch=Request.CutEpoch,
                    Status="incomplete",
                    Diagnostics=(("Reason", "parent-deadline-expired"),),
                ))
                continue
            try:
                Outcomes.append(Futures[Key].result(timeout=Remaining))
            except Exception as Error:
                Outcomes.append(ImmutableEligibilityPreparationOutcome(
                    ComponentVariant=Request.ComponentVariant,
                    QueueOrder=Request.QueueOrder,
                    PlacementFingerprint=Request.PlacementFingerprint,
                    CutEpoch=Request.CutEpoch,
                    Status="incomplete",
                    Diagnostics=(("Exception", repr(Error)),),
                ))
    return tuple(sorted(Outcomes, key=lambda Outcome: Outcome.CanonicalKey))
