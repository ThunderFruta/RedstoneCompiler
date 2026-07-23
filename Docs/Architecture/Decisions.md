# Architecture decisions

## Active decisions

### Template PCB is authoritative

The compiler retains fixed standard-cell templates and exact Redstone physical
rules. Routing algorithms may change; material and simulation truth do not.

### Negotiated routing is the production direction

The normal path uses provisional route trees, exact congestion accounting, and
bounded repair. Exact candidate assignment remains legacy/unit-test support.

### Resource graphs are incremental

Detailed routing builds only guided regions plus halos. Escalation adds region
deltas to a reusable context and must not rebuild a placement-wide graph.

### Failures are typed and hard

Production does not silently switch routers or accept partial physical output.

### Algorithms are circuit agnostic

Names, generated prefixes, gate counts, and benchmark identities cannot select
routing behavior. Only topology, physical capacity, congestion, and policy may
do so.

### Acceptance is sequential

FullAdder gates RCA4; RCA4 gates CLA4; compaction begins only after all three
correctness and determinism gates pass.

