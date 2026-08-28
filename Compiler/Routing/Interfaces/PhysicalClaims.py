"""Exact physical-claim compatibility shared across routing layers.

Keeping these predicates below both component and authoritative routing makes
claim ownership rules identical at the local/global boundary and prevents a
dependency on either solver implementation.
"""

from __future__ import annotations

from typing import Iterable

from ..Contracts.Core import Position3
from ..ResourceGraph import (
    FindSelfClaimConflicts,
    LocalRouteClaim,
    RoutingResourceClaims,
)


def _MergeClaims(
    Values: Iterable[RoutingResourceClaims],
) -> RoutingResourceClaims:
    Items = tuple(Values)
    return RoutingResourceClaims(
        WireCells=frozenset(
            Position for Value in Items for Position in Value.WireCells
        ),
        SupportCells=frozenset(
            Position for Value in Items for Position in Value.SupportCells
        ),
        RequiredAirCells=frozenset(
            Position
            for Value in Items
            for Position in Value.RequiredAirCells
        ),
        ElectricalCells=frozenset(
            Position
            for Value in Items
            for Position in Value.ElectricalCells
        ),
    )


def ComponentClaimsConflict(
    First: RoutingResourceClaims,
    Second: RoutingResourceClaims,
) -> bool:
    """Return exact capacity, electrical, support, or air incompatibility."""
    # This predicate is the inner loop of exact component arc consistency.
    # Keep every ownership rule explicit: constructing temporary unions here
    # multiplies large fabric claim sets for every option pair and can consume
    # an entire component-stage deadline before the CSP starts.  Separate
    # intersections are logically identical and short-circuit without
    # allocating merged sets.
    return bool(
        First.WireCells & Second.WireCells
        or First.SupportCells & Second.WireCells
        or First.SupportCells & Second.RequiredAirCells
        or Second.SupportCells & First.WireCells
        or Second.SupportCells & First.RequiredAirCells
        or First.RequiredAirCells & Second.WireCells
        or Second.RequiredAirCells & First.WireCells
        or First.ElectricalCells & Second.WireCells
        or Second.ElectricalCells & First.WireCells
    )


def ComponentClaimsCompatibleForOwners(
    FirstOwner: str,
    First: RoutingResourceClaims,
    SecondOwner: str,
    Second: RoutingResourceClaims,
) -> bool:
    """Apply electrical rules without exempting same-net physical collisions."""
    if FirstOwner != SecondOwner:
        return not ComponentClaimsConflict(First, Second)
    return not FindSelfClaimConflicts({
        FirstOwner: _MergeClaims((First, Second)),
    })


def ClaimConflictPositions(
    First: RoutingResourceClaims,
    Second: RoutingResourceClaims,
) -> frozenset[Position3]:
    """Return only positions where two route-claim sets are incompatible."""
    Electrical = (First.WireCells & Second.ElectricalCells) | (
        Second.WireCells & First.ElectricalCells
    )
    Support = (
        First.SupportCells & (Second.WireCells | Second.RequiredAirCells)
    ) | (
        Second.SupportCells & (First.WireCells | First.RequiredAirCells)
    )
    Air = (First.RequiredAirCells & Second.WireCells) | (
        Second.RequiredAirCells & First.WireCells
    )
    return frozenset(Electrical | Support | Air)


def MandatoryClaimsConflict(
    First: RoutingResourceClaims,
    Second: RoutingResourceClaims,
) -> bool:
    """Test route-claim incompatibility without allocating position unions."""
    return (
        not First.WireCells.isdisjoint(Second.ElectricalCells)
        or not Second.WireCells.isdisjoint(First.ElectricalCells)
        or not First.SupportCells.isdisjoint(Second.WireCells)
        or not First.SupportCells.isdisjoint(Second.RequiredAirCells)
        or not Second.SupportCells.isdisjoint(First.WireCells)
        or not Second.SupportCells.isdisjoint(First.RequiredAirCells)
        or not First.RequiredAirCells.isdisjoint(Second.WireCells)
        or not Second.RequiredAirCells.isdisjoint(First.WireCells)
    )


def PortalTupleConflictsWithFrozenComponentClaims(
    Signal: str,
    Claims: RoutingResourceClaims,
    FrozenComponentClaims: Iterable[LocalRouteClaim],
) -> tuple[str, ...]:
    """Return immutable routed-component owners blocking one portal tuple.

    The global probe is intentionally narrow, so tuples that cannot coexist
    with the already-frozen routed component must be removed before that
    window is selected.  Same-signal component claims are continuations of
    the route and remain legal here; their combined self-legality is checked
    independently.
    """
    return tuple(sorted({
        Claim.Signal
        for Claim in FrozenComponentClaims
        if (
            Claim.Signal != Signal
            and ComponentClaimsConflict(Claims, Claim.Claims)
        )
    }))
