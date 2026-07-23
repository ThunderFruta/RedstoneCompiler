# Track assignment and boundary capacity

## Contract

Every inter-cluster terminal must have a claim-compatible boundary escape.
Capacity is one for unrelated signals. Same-signal branches may merge.

Boundary matching operates on physical portal claims, not signal names or
benchmark identities. Failure returns a saturated cut containing every
affected terminal, signal, cluster, and resource.

## Detailed assignment

The negotiated router may provisionally overuse resources, but it succeeds only
when exact final ownership has zero conflicts. Exact candidate assignment
remains isolated legacy/unit-test support and is not the normal authoritative
path.

Portal matching must not destroy detailed-route reachability by prematurely
collapsing every terminal to one layer. Pin access, layer choice, and detailed
connectivity must be coordinated or retained as a bounded domain until a legal
tree commits.

## Repeater capacity

Repeater placement reserves its wire, support, required-air, and electrical
claims during search. Search state includes direction and remaining strength.
`RepeaterAccessInfeasible` means no legal state-space route exists in the
current region, not merely that post-processing failed.

