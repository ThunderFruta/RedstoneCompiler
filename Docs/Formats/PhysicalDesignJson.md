# Physical design JSON

`<Name>.PhysicalDesign.json` is the authoritative successful-run evidence
envelope. It is published transactionally only after final claim validation
and litematic serialization. It is not Fabric-server acceptance by itself.

Important sections include:

- `Strategy`, `Policy`, and `Technology` for the selected behavior;
- `SourceState`, `Environment`, and `Reproduction` for provenance;
- `PlanningContracts`, `GlobalGuidePlanning`, and `NegotiatedRouting`;
- `RoutingResourceGraph` cache and graph statistics;
- `RunSummary` for dimensions, runtime, route metrics, and Fabric-server status;
- `FinalValidation` for conflict and unresolved-claim truth; and
- `BlockComposition` for exact material and provenance counts.

Routing acceptance requires `FallbackUsed=false`, zero final conflicts, zero
unresolved claims, and deterministic fingerprints across repeated runs. Full
behavioral acceptance additionally requires an authoritative Fabric-server
result; `FabricServerValidation.Status=not-run` is explicitly unvalidated.
Fields may grow with a policy version; readers should ignore unknown fields and
must not infer server acceptance from file presence alone.

Failed routing writes `<Name>.RoutingFailure.json` instead. Its typed failure,
partial work, cuts, deadlines, and reproduction data are diagnostic evidence,
not a successful physical design.
