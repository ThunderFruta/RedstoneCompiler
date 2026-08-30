# Routing debugging

Start from the typed failure and retained artifact, not from the last console
line. Record the exact command, interpreter, source state, policy, deadline,
and artifact path.

For negotiated-routing failures inspect, in order:

1. boundary escape matching and saturated cuts;
2. coarse overflow progression and history costs;
3. rerouted signals and retained/pruned branches;
4. active tiles, boundary touches, expansions, and cache deltas;
5. wire, support, required-air, electrical, and repeater claims;
6. placement feedback rounds and packed-area growth; and
7. final structural validation and Fabric-server status.

`BoundaryEscapeInfeasible` and congestion cuts should return contributing
clusters to placement. A stable nonzero overflow should expand or repair the
affected region. `RuntimeBudgetExceeded` is a bounded outcome, not evidence
that a larger timeout fixes topology.

Run focused unit tests before repeating a scale compile. Preserve the first
failed circuit's artifact and stop the acceptance sequence there. The
[failure catalog](../Routing/FailureCatalog.md) maps each typed failure to its
required evidence and next owning stage.
