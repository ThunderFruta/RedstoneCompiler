# Arithmetic routing regions

RCA4 and CLA4 are routing acceptance circuits, not algorithm selectors. The
router must infer clusters, terminals, fanout, guides, capacities, and feedback
from topology and geometry; it must never branch on module or generated-net
names.

Run scale tests only after the focused routing checks pass and no other large
compile is active:

```bash
RC_RUN_SCALE_TESTS=1 python3 -m unittest Tests.test_scale_routing -v
```

Judge circuits sequentially:

1. FullAdder must pass 5/5 below 15 seconds.
2. RCA4 must pass 2/2 below 25 seconds with 512/512 rows.
3. CLA4 may then run and must pass 2/2 below 120 seconds with 512/512 rows.

For a failure, retain `.RoutingFailure.json` and inspect overflow progression,
failure cuts, affected clusters, cached graph size, active tiles, and deadline
state. A stable nonzero overflow normally calls for region growth, branch
repair, or placement feedback—not a benchmark-specific exception.

The current flow is specified in the
[negotiated route-tree design](../Routing/NegotiatedRouteTreeRouter.md).
