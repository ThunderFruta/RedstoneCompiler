# Routing failure catalog

All production routing failures use `RoutingFailureReason` and are serialized
to `.RoutingFailure.json`. A failure never publishes a successful litematic.

| Failure | Meaning | Normal response |
| --- | --- | --- |
| `BoundaryEscapeInfeasible` | Capacity-one terminal escape matching has a saturated cut | Relocate every contributing cluster |
| `GlobalCongestionUnresolved` | Coarse or sparse-region routing cannot produce the required connected tree | Expand offender tiles or return a congestion cut |
| `DetailedCongestionUnresolved` | Provisional trees remain in exact claim conflict | Prune offender branches, expand on boundary touch, then relocate on stagnation |
| `RepeaterAccessInfeasible` | No legal directional/strength state can reserve a repeater and its claims | Expand validation/search context or relocate affected endpoints |
| `RuntimeBudgetExceeded` | Shared absolute deadline expired | Preserve partial metrics and stop |
| `TrackAssignmentConflict` | Exact capacity-one ownership cannot be completed | Return conflict graph; do not silently fall back |
| `NoBoundaryEscape` | A terminal has no portal geometry on any legal layer | Reject or relocate placement |
| `ElectricalConflict` | Different signals violate Redstone electrical isolation | Reroute or relocate; never waive |
| `SupportConflict` | A support claim conflicts with another route | Reroute with support occupancy enabled |
| `HeadroomConflict` | Required air is occupied | Reroute or change layer transition |
| `NoRepeaterSite` | Signal strength cannot be restored legally | Treat as repeater-access failure during stateful search |
| `FinalDrcViolation` | Final materialized design fails authoritative validation | Reject routed result |

Every failure should include stage, affected nets, resources, locations,
bounded repair actions, deadline state, and the relevant conflict or boundary
cut.

See [Current routing failures](CurrentRoutingFailures.md) for the active RCA4
failure sequence and the distinction between observed facts and hypotheses.
