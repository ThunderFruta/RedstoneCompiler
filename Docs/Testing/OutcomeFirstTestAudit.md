# Outcome-first active-test audit

## Decision rule and accounting

This audit reviewed every executable test collected under `Tests/` before the
parallel physical-design rewrite. The pre-audit collection was 1,450 cases; the
post-audit collection is 1,407 cases. The difference is 43 removals. Counts are
accounting only, never a minimum or success gate.

The post-audit AST inventory contains 1,387 executable test definitions and no
exact duplicate-body groups. The 20 additional collected cases come from
parameterization. Exact-body uniqueness did not by itself justify retention;
the observable-contract review below is the deciding record.

Every retained test is covered by the file-level ownership register below and
must state its specific contract in its pytest node name. Parameterized nodes
represent distinct input or failure classes under the same stated contract.
Retention requires an independently observable result: an oracle result,
physical/electrical legality, a typed failure, deterministic public output,
artifact integrity, external-boundary behavior, or a documented dependency or
public-owner rule. A mock is acceptable only when it isolates that boundary and
the asserted result remains public.

The canonical executable inventory is produced by:

```bash
.venv/bin/python -m pytest --collect-only -q Tests
```

## Retained ownership register

The count in each row accounts for every surviving collected node in that file.
The contract column states why each named test has unique regression value; its
node name supplies the narrower behavior or failure class.

| Test owner | Cases | Unique retained contract and observable failure |
|---|---:|---|
| `App/test_main_paths.py` | 9 | Public flags, deterministic test-tier isolation, truthful progress/report fallback, and immutable artifact promotion. |
| `App/test_routing_telemetry.py` | 7 | CPU/process accounting, task error propagation, and partial telemetry that cannot fabricate completion. |
| `App/test_run_reporting.py` | 5 | Durable report ordering/evidence, safe environment capture, atomic promotion, and terminal tee behavior. |
| `Compiler/Frontend/test_sv_parser_failures.py` | 4 | Unsupported or ambiguous SystemVerilog fails closed with a public diagnostic. |
| `Compiler/Synthesis/test_adder_arithmetic_oracles.py` | 5 | Independently computed arithmetic truth tables and rename/order invariance. |
| `Compiler/Synthesis/test_component_graph.py` | 3 | Closed component ownership/endpoints and structure-based partition invariance. |
| `Compiler/Synthesis/test_logic_optimization.py` | 2 | Observable NAND-count non-regression and removal of a redundant equation. |
| `Compiler/Synthesis/test_nand_differential.py` | 1 | Random acyclic logic remains truth-equivalent after NAND lowering. |
| `Integration/test_local_first_router.py` | 31 | Public placement/routing outputs, legality, deterministic identities, bounded guides, failure reporting, and a real FullAdder publication slice. |
| `Integration/test_pipeline_artifact_integrity.py` | 10 | Success/failure artifacts are consistent, stale evidence is removed, and only an observed server snapshot becomes canonical. |
| `Integration/test_router_reliability.py` | 87 | Public reliability state, bounded work/deadlines, deterministic fingerprints, invalidation, typed exhaustion, and stable failure artifacts. |
| `Integration/test_scale_routing.py` | 3 | Opt-in RCA4/RCA8/CLA4 runs traverse the real pipeline independently without fail-fast masking. |
| `PhysicalDesign/Placement/test_access_contract_bounds.py` | 3 | Access rejects incomplete, out-of-bounds, or identity-inconsistent domains. |
| `PhysicalDesign/Placement/test_cla4_access_replay.py` | 1 | Tracked historical access failure replay; diagnostic evidence, not a production special case. |
| `PhysicalDesign/Placement/test_derived_perimeter_access_fabric.py` | 16 | Perimeter fabric completeness, capacity, determinism, connectivity, and typed failure. |
| `PhysicalDesign/Placement/test_derived_perimeter_slots.py` | 2 | Boundary slots obey actual cell geometry and rotation. |
| `PhysicalDesign/Placement/test_fixed_pin_access_solver.py` | 5 | Fixed pins receive exact feasible access or sound typed infeasibility. |
| `PhysicalDesign/Placement/test_joint_cluster_orientation.py` | 4 | Joint orientation preserves pin access and deterministic legal placement. |
| `PhysicalDesign/Placement/test_physical_cells.py` | 21 | Cell transforms, pins, supports, claims, and electrical isolation match Minecraft geometry. |
| `PhysicalDesign/Placement/test_pin_aligned_packed_cluster_portfolio.py` | 3 | Packed alternatives expose distinct legal pin-aligned outcomes without name dependence. |
| `PhysicalDesign/Placement/test_placement_access_fabric.py` | 20 | Placement produces complete deterministic access with exact capacity/ownership. |
| `PhysicalDesign/Placement/test_placement_boundary_feasibility.py` | 75 | Boundary feasibility, relocation cuts, exact domains, portfolio completeness, and incomplete/unsat separation. |
| `PhysicalDesign/Rendering/test_schem_roundtrip.py` | 1 | Real litematic round-trip of states, coordinates, signs, and I/O labels. |
| `PhysicalDesign/Routing/test_authoritative_assignments.py` | 61 | Capacity-one assignment, exact clauses, deterministic selection, and sound completeness. |
| `PhysicalDesign/Routing/test_authoritative_caches.py` | 80 | Reuse only on matching dependencies/revalidated geometry; corruption and stale/incomplete state fail closed. |
| `PhysicalDesign/Routing/test_authoritative_deadlines.py` | 20 | Bounded deadlines stop work without converting exhaustion into unsat/success and retain diagnostics. |
| `PhysicalDesign/Routing/test_authoritative_exterior_distance.py` | 9 | Exterior distance matches geometry/transforms and types unreachable states. |
| `PhysicalDesign/Routing/test_authoritative_global_routes.py` | 132 | Exact claims/capacity, no-good reasoning, global/local handoff, convergence, signal strength, and deterministic route outputs. |
| `PhysicalDesign/Routing/test_authoritative_guide_stage.py` | 2 | Guide output is deterministic and cannot claim detailed-route legality. |
| `PhysicalDesign/Routing/test_authoritative_portals.py` | 69 | Portal completeness, ownership, transforms, support, and rejection of stale/corrupt witnesses. |
| `PhysicalDesign/Routing/test_channel_planner.py` | 3 | Channel plans honor obstacles/capacity and expose deterministic typed overflow. |
| `PhysicalDesign/Routing/test_component_pipeline_cache_lifetime.py` | 15 | Component caches obey dependency lifetime and cannot reuse stale/incomplete proofs. |
| `PhysicalDesign/Routing/test_component_pipeline_orchestration.py` | 19 | Public component results and typed failures across bounded stages. |
| `PhysicalDesign/Routing/test_component_pipeline_proof_scheduling.py` | 55 | Proof scheduling preserves completeness, cancellation, fairness, and settled identity under permutation. |
| `PhysicalDesign/Routing/test_component_pipeline_repair_queues.py` | 39 | Repair queues make bounded signal-scoped progress and cannot manufacture completion. |
| `PhysicalDesign/Routing/test_component_planning.py` | 20 | Small domains match independent solvers/oracles and preserve exact capacity/identity. |
| `PhysicalDesign/Routing/test_component_profile_projection.py` | 5 | Projection is anonymous/deterministic and invalidated by physical dependencies. |
| `PhysicalDesign/Routing/test_component_router.py` | 81 | Bounded routing returns electrically legal paths or typed failures, with native differential checks. |
| `PhysicalDesign/Routing/test_component_symbolic_factor_state_contract.py` | 5 | Factor state round-trips semantic progress without stale/partial proof promotion. |
| `PhysicalDesign/Routing/test_component_symbolic_higher_order_domain.py` | 9 | Higher-order domains preserve exact tuples, completeness, and identity. |
| `PhysicalDesign/Routing/test_eligibility_preparation.py` | 3 | Eligibility rejects stale/incomplete work and admits matching requests. |
| `PhysicalDesign/Routing/test_local_factor_unsat_projection.py` | 4 | UNSAT pruning requires complete exact name-independent proof identity. |
| `PhysicalDesign/Routing/test_physical_assembly_exact_proofs.py` | 27 | Assembly commits only exact complete proofs and rejects stale/incomplete certificates. |
| `PhysicalDesign/Routing/test_physical_assembly_fabric.py` | 58 | Physical fabrics are connected, capacity-safe, claim-complete, transform-correct, or fail typed. |
| `PhysicalDesign/Routing/test_physical_assembly_global_handoff.py` | 52 | Global and local stages consume one immutable identity and reject bypassed/stale handoffs. |
| `PhysicalDesign/Routing/test_physical_assembly_port_domains.py` | 33 | Port domains are complete, exact, capacity-one, and preserve endpoint alternatives. |
| `PhysicalDesign/Routing/test_physical_component_models.py` | 5 | Public models reject malformed ownership, stage, and completeness. |
| `PhysicalDesign/Routing/test_pre_route_interface.py` | 14 | Pre-route selection exposes deterministic feasibility/typed rejection without final success. |
| `PhysicalDesign/Routing/test_repeater_orientation_contract.py` | 2 | Repeater direction follows Minecraft input-facing semantics and mismatches fail. |
| `PhysicalDesign/Routing/test_resource_graph.py` | 16 | Claims enforce capacity, electrical exclusion, deterministic ownership, and graph identity. |
| `PhysicalDesign/Routing/test_routing_contract_schema.py` | 1 | Neutral contracts cannot import orchestration/routing implementations. |
| `PhysicalDesign/Routing/test_routing_failures.py` | 7 | Failures serialize stable public stages/details without losing nested diagnostics. |
| `PhysicalDesign/Routing/test_routing_policy_generic_profile.py` | 1 | Policy decisions are invariant to circuit and signal names. |
| `PhysicalDesign/Routing/test_routing_resources.py` | 14 | Wires/supports/repeaters satisfy exact resource and electrical claims. |
| `PhysicalDesign/Routing/test_template_track_assignment.py` | 12 | Template tracks obey capacity, orientation, connectivity, and deterministic assignment. |
| `PhysicalDesign/Routing/test_topology_demand_profile.py` | 63 | Demand profiles respond to graph geometry and remain rename/order invariant. |
| `Structural/test_routing_architecture.py` | 2 | Technology connectivity/layer mapping and missing physical-pin rejection. |
| `Structural/test_routing_design_snapshot.py` | 11 | Evidence is provenance-stable, mutation-detecting, fresh, portable where promised, and rejects mixed/malformed inputs. |
| `Structural/test_source_review.py` | 1 | Advisory review is deterministic and cannot silently become a release pass/fail gate. |
| `Structural/test_source_structure.py` | 6 | Documented retired paths/imports, one-way dependencies, public owners/exports, and repository acyclicity. |
| `Tools/test_repeater_orientation_smoke.py` | 1 | Operational smoke reports the public repeater-orientation verdict. |
| `Tools/test_router_acceptance_harness.py` | 62 | Acceptance rejects forged/stale/incomplete evidence, preserves every case, checks artifacts/provenance/performance, and never promotes non-Fabric simulation. |
| `Tools/test_script_cli_guidance.py` | 9 | Clear confirmation, live mismatch, vector selection/sequencing, existing-world non-mutation, and observed snapshot publication. |
| `Validation/Fabric/test_fabric_server_boundary.py` | 29 | Fabric absence is typed infrastructure failure; only observed results pass; fixture/vector/trace/timeout/world boundaries fail closed. |
| `Validation/Fabric/test_fabric_server_console.py` | 4 | Authenticated console rejects unsafe commands and honors the selected runtime. |
| `Validation/Fabric/test_fabric_server_runtime_manager.py` | 8 | Ownership/recovery and world clearing are path-scoped, reversible, and cannot create/mutate another world. |
| `Validation/Fabric/test_fabric_server_snapshot.py` | 5 | Snapshot forces inputs low, preserves observed state/bounds, chunks reads, and fails on omissions. |
| `Validation/Fabric/test_schem_import.py` | 5 | Real decoding preserves palette order, dynamic state, ports/entities or rejects unsupported input. |
| `Validation/Mchprs/test_mchprs_validation.py` | 6 | Hash-bound FullAdder/RCA8 fixtures pass independent exhaustive truth tables and deterministic vector policy. |
| `WorktreeSetup/test_worktree_setup.py` | 4 | Interpreter/native provenance, real MCHPRS, and shared/explicit Fabric runtime classification. |

## Removed and rewritten tests

| Old test identifier(s) | Disposition and category | Stronger retained coverage or no-replacement reason |
|---|---|---|
| `test_routing_contract_schema_matches_pre_split_baseline` | Removed: brittle implementation coupling; class count plus introspection hash. | Versioned documents are exercised by owning producers/consumers; one-way dependency remains. |
| `test_configured_paths_are_owned_by_this_checkout`; `test_worktree_setup_configures_an_isolated_linux_environment` | Removed: redundant inventory and script spelling. | Interpreter/native provenance, real MCHPRS, and Fabric behavior test setup outcomes. |
| `test_native_extension_and_editor_stub_share_the_package_boundary` | Removed with its file: duplicate packaging smoke. | Provenance loads this checkout; real MCHPRS/native tests execute exports. Suffix/stub spelling is not correctness. |
| `testRepositoryPythonDoesNotReferenceRetiredImportNames` | Removed: brittle source-text duplicate. | AST-resolved retired imports and retired paths enforce the documented clean break. |
| `testAuthoritativePhaseRunnerAcceptsExplicitServices` | Removed: private orchestration injection. | Public authoritative orchestration outcomes, failures, and handoffs remain. |
| `testImportCycleDetectorFindsStronglyConnectedComponents` | Removed: tests the test helper. | `testCompilerImportsAreAcyclic` applies it to the real graph. |
| `testAuthoritativeRoutingUsesSingleConfiguredAttempt`; `testPolicySnapshotIsTypedAndRejectsGuideFreeAttempts`; `testDefaultPolicyEnablesAuthoritativeRoutingFeatures`; `testAbsoluteMaterialGatesAreBenchmarkOnly` | Removed: arbitrary literal/policy snapshots. | Guide, placement, routing, acceptance, and benchmark tests cover resulting behavior. |
| `testRootEntrypointExclusivelyOwnsGuidedCli`; `testRootGuidedCliNestsDefaultsAndPushUnderMoreOptions`; `testRootFlagCliDelegatesWithoutOpeningGuidedMenu` | Removed: menu layout/private dispatch. | Public parsing, compile/report, pytest isolation, and benchmark behavior remain. |
| `testFabricControlUsesTheCanonicalRuntimeManager`; `testFabricControlDispatchesToTheManagerPackage` | Removed: wrapper ownership/call coupling. | Fabric root, authentication, ownership, and status behavior remain. |
| `testFabricControlGuidesLifecycleActions` | Rewritten as `testFabricControlRequiresExplicitClearConfirmation`. | Retains only the destructive safety boundary. |
| `testFabricImportGuidesToHotReload`; `testFabricImportDefaultsToTheCanonicalServerRoot`; `testFabricImporterHonorsTheSharedRootOverride` | Removed: prompt/default snapshots and duplicated root resolution. | Observed snapshot publication and Fabric root/environment tests remain. |
| `testFabricTesterGuidesToTheImportedSchematic`; `testFabricTesterGuidesToOneTruthTableRow`; `testFabricTesterGuidesToAllRowsOneAtATime`; `testFabricTesterGuidesToExistingWorldStateWithAnSvOracle`; `testFabricTesterDefaultsToTheCanonicalServerRoot`; `testFabricTesterPromptsForEnterBetweenSequentialRows` | Removed: cosmetic prompt/default snapshots. | Parser exclusion, vector behavior, mismatch, and existing-world safety remain. |
| `testRoutingGuideDefaultsToDefaultRunWithoutPreviews`; `testSnapshotGuideCollectsExplicitInputPaths` | Removed: guided argument spelling. | Acceptance/snapshot publication outcomes remain. |
| `testPythonDefinitionMetricsIncludeNestedQualifiedSpans`; `testNandDiagramSummaryCountsKinds`; `testSourceManifestIsDeterministicAndResolvesInventory` | Removed: internal source/snapshot analysis. | Publication, provenance, mutation, malformed-input, mixed-evidence, and artifact identity remain. |
| `testInitialPlacementConsumesOneConflictRelocation`; `testInitialPlacementRetargetsOneChangedConflictSet`; `testInitialPlacementConsumesOneQueuedExactJointState` | Removed: private helper calls/state/event spelling. | Placement feasibility and reliability retain signal-scoped progress, exact cuts, and outcomes. |
| `testGraphBeamIsNotConstructedAfterPrimaryCandidateRoutes`; `testFullFootprintSelectionUsesFinalRenderedComposition`; `testConfiguredGraphBeamIsNotTriedAfterPrimaryPlacementFails`; `testSingleAttemptDoesNotRouteASecondPlacementAfterFailure`; `testPressuredPlacementDoesNotFailOverToDeferredAlternative` | Removed: mocked/bypassed placement-to-routing boundary and call counts. | Real legality, pre-route interface, publication, scale, and FullAdder pipeline tests remain. |
| `testCliDeadlineOverridesEffectivePolicyWithoutMutatingCanonicalPolicy`; `testNewRouterFirstSurfacesLocalRoutingFailure` | Removed: private runner interception/effective-policy inspection. | Public CLI failure artifacts and reliability deadline/failure suites remain. |
| `testRemovedHybridStrategyIsRejectedBeforeRouting`; `testRemovedCompatibilityStrategyIsRejectedBeforeRouting` | Rewritten and deduplicated as `testRemovedStrategiesAreRejected`. | One public-result test covers both unsupported legacy inputs without private-runner interception. |
| `test_no_hardcoded_circuit_exceptions_in_active_compiler_code` | Removed: source spelling scan. | Rename/order-invariance and generic-profile behavior enforce circuit agnosticism. |
| `testHarnessConfigurationDefaultsToOneThousandTps` | Removed: exact unversioned literals. | Runtime safety/live boundaries remain; capacity is measured acceptance evidence. |
| `testCarryLookaheadAdder4MatchesOracleAndNandCheckpoint`; shared adder digest assertions | Rewritten as arithmetic-oracle tests without fixed NAND count or output digest. | Every row is still compared directly to independent integer addition; metamorphic NAND structure remains rename/order invariant. |

## Acceptance boundary

This cleanup does not lower acceptance. Deterministic unit/contract tests cannot
claim production routing success. MCHPRS remains the fast physical truth-table
gate, the scale tier retains all three outcomes without fail-fast, and a fresh
live Fabric run remains the final Minecraft semantic canary.
