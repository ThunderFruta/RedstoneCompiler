"""Repair Queues contracts for component pipeline."""

from ._component_pipeline_contracts import *


def test_topology_repaired_descendant_uses_alternate_joint_branch():
    Select = (
        PlacementPhysicalAssembly
        .SelectInheritedTopologyJointPlacementCandidateIndex
    )

    assert Select("split-interface-cut") == 1
    assert Select("relocate-endpoint-cluster") == 1
    assert Select(
        "relocate-endpoint-cluster",
        ComposedSignalCount=3,
    ) == 0
    assert Select("") == 0

def test_composed_capacity_repair_requires_the_complete_signal_domain():
    Composed = SimpleNamespace(
        RepairLevel="local-assembly",
        ProofKind="composed-complete-capacity-core",
        Signals=("CarryIn", "Generate1", "B1"),
    )
    Fresh = SimpleNamespace(
        RepairLevel="local-assembly",
        ProofKind="complete-capacity-core",
        Signals=("Generate1", "B1"),
    )

    assert PlacementPhysicalAssembly.SelectCapacityRepairGenerationSignals(
        Composed,
        ("Generate1", "B1"),
    ) == frozenset(("CarryIn", "Generate1", "B1"))
    assert PlacementPhysicalAssembly.SelectCapacityRepairGenerationSignals(
        Fresh,
        ("Generate1", "B1"),
    ) == frozenset(("Generate1", "B1"))
    assert PlacementPhysicalAssembly.SelectCapacityRepairTransactionalSignals(
        Composed,
        ("Generate1", "B1"),
    ) == frozenset(("CarryIn", "Generate1", "B1"))
    assert PlacementPhysicalAssembly.SelectCapacityRepairTransactionalSignals(
        Fresh,
        ("Generate1", "B1"),
    ) == frozenset(("Generate1", "B1"))

def test_transactional_capacity_repair_rejects_incomplete_or_broad_domains():
    UnrelatedFocus = SimpleNamespace(
        RepairLevel="local-assembly",
        ProofKind="composed-complete-capacity-core",
        Signals=("CarryIn", "Generate1", "B1"),
    )
    Broad = SimpleNamespace(
        RepairLevel="local-assembly",
        ProofKind="composed-complete-capacity-core",
        Signals=("CarryIn", "Generate1", "B1", "B2"),
    )
    Channel = SimpleNamespace(
        RepairLevel="channel-capacity",
        ProofKind="composed-complete-capacity-core",
        Signals=("CarryIn", "Generate1", "B1"),
    )

    assert not PlacementPhysicalAssembly.SelectCapacityRepairTransactionalSignals(
        UnrelatedFocus,
        ("Generate0", "B0"),
    )
    assert not PlacementPhysicalAssembly.SelectCapacityRepairTransactionalSignals(
        Broad,
        ("Generate1", "B1"),
    )
    assert not PlacementPhysicalAssembly.SelectCapacityRepairTransactionalSignals(
        Channel,
        ("Generate1", "B1"),
    )

def test_complete_three_signal_repair_composes_one_bounded_descendant():
    assert PlacementPhysicalAssembly.SelectCapacityRepairTransactionalVariants(
        0,
        ("B1", "CarryIn", "Generate1"),
    ) == (0, 1)
    assert PlacementPhysicalAssembly.SelectCapacityRepairTransactionalVariants(
        1,
        ("B1", "CarryIn", "Generate1"),
    ) == (1,)
    assert PlacementPhysicalAssembly.SelectCapacityRepairTransactionalVariants(
        0,
        ("CarryIn", "Generate1"),
    ) == (0,)
    assert not PlacementPhysicalAssembly.SelectCapacityRepairTransactionalVariants(
        2,
        ("B1", "CarryIn", "Generate1"),
    )

def test_complete_three_signal_repair_prefetches_only_one_internal_signal():
    Select = (
        PlacementPhysicalAssembly
        .SelectCapacityRepairCumulativeSingletonPrefetchSignal
    )

    assert Select(
        ("B1", "CarryIn", "Generate1"),
        ("B1", "CarryIn", "Sum0"),
    ) == "Generate1"
    assert not Select(
        ("B1", "Generate1", "Propagate1"),
        ("B1", "CarryIn", "Sum0"),
    )
    assert not Select(
        ("B1", "CarryIn"),
        ("B1", "CarryIn", "Sum0"),
    )
    assert not Select(
        ("A1", "B1", "CarryIn"),
        ("A1", "B1", "CarryIn", "Sum0"),
    )

def test_learned_transition_replay_requires_one_advancing_signal():
    Select = (
        PlacementPhysicalAssembly
        .SelectLearnedAdvancingSingletonRepairTransition
    )
    Build = (
        PlacementPhysicalAssembly
        .BuildSingletonLocalFactorRepairTransitionKey
    )
    NandNet4 = Build(
        "NandNet4",
        1,
        {
            "SelectedClusterIndices": [3],
            "InvalidatedSignals": [
                "NandNet4",
                "NandNet5",
                "Propagate1",
            ],
        },
    )
    NandNet5 = Build(
        "NandNet5",
        1,
        {
            "SelectedClusterIndices": [3],
            "InvalidatedSignals": [
                "NandNet4",
                "NandNet5",
                "Propagate1",
            ],
        },
    )

    assert Select({
        NandNet4: "NandNet5",
        NandNet5: "NandNet5",
    }) == (NandNet4, "NandNet5")
    assert not Select({NandNet4: "<ambiguous>"})
    assert not Select({
        NandNet4: "NandNet5",
        Build(
            "Generate1",
            1,
            {
                "SelectedClusterIndices": [5],
                "InvalidatedSignals": [
                    "Generate1",
                    "NandNet19",
                    "Propagate2",
                ],
            },
        ): "NandNet4",
    })

def test_proof_closed_learned_transition_sorts_behind_fresh_sibling():
    Classify = (
        PlacementPhysicalAssembly
        .ClassifyLearnedTransitionCandidatePriority
    )
    Prefetched = frozenset(("prefetched",))
    Closed = frozenset(("closed",))

    assert tuple(sorted(
        ("closed", "fresh", "prefetched"),
        key=lambda Fingerprint: Classify(
            Fingerprint,
            Prefetched,
            Closed,
        ),
    )) == ("prefetched", "fresh", "closed")
    assert Classify("prefetched", Prefetched, Closed) == 0
    assert Classify("fresh", Prefetched, Closed) == 1
    assert Classify("closed", Prefetched, Closed) == 2

def test_learned_binary_transition_prefetches_only_alternate_sibling():
    Select = (
        PlacementPhysicalAssembly
        .SelectAlternateBinarySingletonRepairVariant
    )

    assert Select(0) == 1
    assert Select(1) == 0
    assert Select(-1) is None
    assert Select(2) is None

def test_minimal_physical_port_core_builds_explicit_placement_feedback():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        AffectedNets=("UnusedBroadSignal",),
        Diagnostics={
            "PortAssignmentProofComplete": True,
            "PortAssignmentUnsatCoreMinimal": True,
            "PortAssignmentUnsatCoreSignals": ["Beta", "Alpha", "Beta"],
            "PortAssignmentUnsatCoreFingerprint": "physical-core",
            "PhysicalAssemblyPlanFingerprint": "plan",
            "DomainFingerprint": "domain",
        },
    )

    Feedback = BuildPhysicalComponentPlacementFeedback(Failure)

    assert Feedback is not None
    assert Feedback.ProofFingerprint == "physical-core"
    assert Feedback.RelocationSignals == ("Alpha", "Beta")
    assert Feedback.SourcePlanFingerprint == "plan"
    assert Feedback.DomainFingerprint == "domain"

def test_complete_singleton_access_core_builds_placement_feedback():
    Feedback = BuildPhysicalComponentPlacementFeedback(RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="ComponentAccessCertification",
        AffectedNets=("NandNet",),
        Diagnostics={
            "Complete": True,
            "Feasible": False,
            "AffectedSignals": ["NandNet"],
            "CertificateFingerprint": "access-core",
            "DomainFingerprint": "access-domain",
        },
    ))

    assert Feedback is not None
    assert Feedback.ProofFingerprint == "access-core"
    assert Feedback.RelocationSignals == ("NandNet",)
    assert Feedback.DomainFingerprint == "access-domain"

def test_complete_ownership_core_is_serialized_and_drives_feedback():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalEligibilitySolveAfterUnarySupport",
        AffectedNets=("NandNet26", "CarryIn"),
        Resources=("portal-a",),
        Locations=((3, 7, 2),),
        Diagnostics={
            "OwnershipUnsatCoreFingerprint": "44136fa355b3678a",
        },
    )
    Core = BuildComponentRoutabilityCore(
        Failure,
        PlacementStateFingerprint="placement",
        ComponentStateFingerprint="component",
        DomainFingerprint="domain",
        CoreFingerprint="fallback",
        Complete=True,
    )

    assert Core is not None
    assert Core.Signals == ("CarryIn", "NandNet26")
    assert Core.BlockingResources == ("portal-a",)
    assert Core.BlockingPorts == ((3, 7, 2),)
    Feedback = BuildPhysicalComponentPlacementFeedback(replace(
        Failure,
        Diagnostics={"ComponentRoutabilityCore": Core.ToDictionary()},
    ))
    assert Feedback is not None
    assert Feedback.ProofFingerprint == "44136fa355b3678a"
    assert Feedback.RelocationSignals == ("CarryIn", "NandNet26")

def test_complete_capacity_pressure_core_overrides_broad_ownership_feedback():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        AffectedNets=("CarryIn", "NandNet26", "NandNet28", "NandNet29"),
        Diagnostics={
            "OwnershipUnsatCoreFingerprint": "44136fa355b3678a",
            "PlacementInterfacePressureSignals": [
                "NandNet29",
                "NandNet28",
            ],
        },
    )

    Core = BuildComponentRoutabilityCore(
        Failure,
        PlacementStateFingerprint="placement",
        ComponentStateFingerprint="component",
        DomainFingerprint="domain",
        CoreFingerprint="fallback",
        Complete=True,
    )

    assert Core is not None
    assert Core.CoreFingerprint != "44136fa355b3678a"
    assert Core.Signals == ("NandNet28", "NandNet29")
    Feedback = BuildPhysicalComponentPlacementFeedback(replace(
        Failure,
        Diagnostics={"ComponentRoutabilityCore": Core.ToDictionary()},
    ))
    assert Feedback is not None
    assert Feedback.RelocationSignals == ("NandNet28", "NandNet29")

def test_proven_capacity_repair_chain_composes_parent_and_child_cores():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(
            Placed=SimpleNamespace(
                LocalRouteClaims=(),
                PlacedGates=(
                    SimpleNamespace(
                        Name="Gate62",
                        Inputs=("Carry", "Net36"),
                        InputPins=((1, 1, 1), (1, 1, 2)),
                        Outputs=("Net38",),
                        OutputPin=(1, 1, 3),
                    ),
                    SimpleNamespace(
                        Name="Gate63",
                        Inputs=("Net37", "Net38"),
                        InputPins=((2, 1, 1), (2, 1, 2)),
                        Outputs=("Sum",),
                        OutputPin=(2, 1, 3),
                    ),
                ),
            ),
            Clusters=((), ("Gate62",), ("Gate63",)),
        ),
    )
    Parent = BuildPhysicalInterfaceRepairCore(
        RoutingFailure(
            Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
            Stage="PhysicalSymbolicCapacityPlacementFeedback",
            Diagnostics={
                "SymbolicCapacityPlacementFeedback": True,
                "SymbolicCapacityProofComplete": True,
                "SymbolicCapacityProofFingerprint": "parent-proof",
                "PlacementInterfacePressureSignals": ["Carry", "Net36"],
                "LocalCapacityCoreClause": [
                    ["Carry", "carry-seam"],
                    ["Net36", "net36-seam"],
                ],
                "SelectedComponentClusters": [1, 2],
            },
        ),
        Candidate,
    )
    Child = BuildPhysicalInterfaceRepairCore(
        RoutingFailure(
            Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
            Stage="PhysicalComponentAssemblyPlanning",
            Diagnostics={
                "PortAssignmentProofComplete": True,
                "PortAssignmentUnsatCoreMinimal": True,
                "PortAssignmentUnsatCoreFingerprint": "child-proof",
                "PortAssignmentUnsatCoreSignals": ["Net36", "Net37"],
                "SelectedComponentClusters": [1, 2],
            },
        ),
        Candidate,
    )
    assert Parent is not None
    assert Child is not None

    First = ComposePhysicalInterfaceRepairCores(Parent, Child, Candidate)
    Second = ComposePhysicalInterfaceRepairCores(Child, Parent, Candidate)

    assert First == Second
    assert First.Signals == ("Carry", "Net36", "Net37")
    assert First.ClusterIds == (1, 2)
    assert First.ComponentGateNames == ("Gate62", "Gate63")
    assert First.ProofKind == "composed-complete-capacity-core"
    ClosurePlacement = SimpleNamespace(
        Clusters=(("Producer",), ("Other",), ("Gate62", "Gate63")),
        ClusterBoundaryLeaseRequests=(
            SimpleNamespace(
                Signal="Carry",
                SourceCluster=0,
                TargetCluster=2,
            ),
            SimpleNamespace(
                Signal="Net36",
                SourceCluster=1,
                TargetCluster=2,
            ),
        ),
        Placed=SimpleNamespace(ClusterBoundaryLeaseRequests=()),
    )
    assert BuildCapacityRepairEndpointClosureClusters(
        ClosurePlacement,
        First,
    ) == (0, 1, 2)
    assert BuildCapacityRepairEndpointClosureClusters(
        ClosurePlacement,
        Parent,
    ) == ()

def test_complete_single_signal_capacity_core_does_not_admit_geometry_repair():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        Diagnostics={
            "PortAssignmentProofComplete": True,
            "PortAssignmentUnsatCoreMinimal": True,
            "PortAssignmentUnsatCoreFingerprint": "single-proof",
            "PortAssignmentUnsatCoreSignals": ["Alpha"],
            "PortAssignmentUnsatCoreClause": [["Alpha", "seam-alpha"]],
        },
    )
    Constraint = BuildPhysicalInterfaceRepairCore(Failure, Candidate)
    assert Constraint is None

def test_complete_singleton_assembly_core_admits_local_factor_diversification():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        Diagnostics={
            "PortAssignmentProofComplete": True,
            "PortAssignmentUnsatCoreMinimal": True,
            "PortAssignmentUnsatCoreFingerprint": "singleton-proof",
            "PortAssignmentUnsatCoreSignals": ["Alpha"],
            "DomainFingerprint": "factor-domain",
        },
    )

    First = BuildPhysicalLocalFactorDiversificationCore(Failure, Candidate)
    Second = BuildPhysicalLocalFactorDiversificationCore(Failure, Candidate)

    assert First == Second
    assert First is not None
    assert First.Signal == "Alpha"
    assert First.SourceProofFingerprint == "singleton-proof"

def test_incomplete_singleton_assembly_core_cannot_diversify_local_factor():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        Diagnostics={
            "PortAssignmentUnsatCoreMinimal": True,
            "PortAssignmentUnsatCoreFingerprint": "singleton-proof",
            "PortAssignmentUnsatCoreSignals": ["Alpha"],
            "DomainFingerprint": "factor-domain",
        },
    )

    assert BuildPhysicalLocalFactorDiversificationCore(
        Failure,
        Candidate,
    ) is None

def test_complete_singleton_typed_seam_failure_admits_endpoint_diversification():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPerimeterSeamUnsatisfiable,
        Stage="ComponentAccessCertification",
        AffectedNets=("Alpha",),
        Diagnostics={
            "AffectedSignals": ["Alpha"],
            "CertificateFingerprint": "access-proof",
            "Complete": True,
            "StructuralFingerprint": "access-domain",
        },
    )

    Core = BuildPhysicalLocalFactorDiversificationCore(Failure, Candidate)

    assert Core is not None
    assert Core.Signal == "Alpha"
    assert Core.SourceProofFingerprint == "access-proof"
    assert Core.LocalFactorIdentityFingerprint

def test_complete_singleton_symbolic_capacity_proof_admits_local_factor_diversification():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage='PhysicalSymbolicCapacityPlacementFeedback',
        AffectedNets=('NandNet4',),
        Diagnostics={
            'SymbolicCapacityPlacementFeedback': True,
            'SymbolicCapacityProofComplete': True,
            'SymbolicCapacityProofFingerprint': 'symbolic-singleton-proof',
            'PlacementInterfacePressureSignals': ['NandNet4'],
            'LocalCapacityCoreClause': [[
                'NandNet4',
                'nand-net-4-seam',
            ]],
        },
    )

    Core = BuildPhysicalLocalFactorDiversificationCore(Failure, Candidate)

    assert Core is not None
    assert Core.Signal == 'NandNet4'
    assert Core.SourceProofFingerprint == 'symbolic-singleton-proof'
    assert Core.LocalFactorIdentityFingerprint

def test_complete_singleton_physical_eligibility_empty_bank_admits_diversification():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalComponentEligibility",
        AffectedNets=("B1",),
        Diagnostics={
            "Complete": True,
            "Feasible": False,
            "ComponentFabricConstructionComplete": True,
            "OwnershipSearchComplete": True,
            "ImplicitForeignTransitDomainCount": 0,
            "PriorityPreparationSignals": ["B1", "CarryIn"],
            "DomainDiagnosticsBySignal": {
                "B1": {
                    "Reason": (
                        "complete-certified-domain-empty-after-physical-projection"
                    ),
                    "RequiredPortLayer": 2,
                    "CertifiedGuideDisconnectedCount": 204,
                },
                "CarryIn": {
                    "Reason": "available-certified",
                    "RequiredPortLayer": 1,
                },
            },
        },
    )

    First = BuildPhysicalLocalFactorDiversificationCore(Failure, Candidate)
    Second = BuildPhysicalLocalFactorDiversificationCore(Failure, Candidate)

    assert First == Second
    assert First is not None
    assert First.Signal == "B1"
    assert First.SourceProofFingerprint
    assert First.LocalFactorIdentityFingerprint

def test_complete_physical_eligibility_recovers_exact_repair_terminals():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage='PhysicalComponentEligibility',
        AffectedNets=('B1',),
        Diagnostics={
            'Complete': True,
            'Feasible': False,
            'ComponentFabricConstructionComplete': True,
            'OwnershipSearchComplete': True,
            'DomainDiagnosticsBySignal': {
                'B1': {
                    'Reason': (
                        'complete-certified-domain-empty-after-physical-'
                        'projection'
                    ),
                    'CandidateCountByTerminal': [
                        {'Terminal': [11, 1, 37], 'CandidateCount': 1},
                        {'Terminal': [18, 1, 39], 'CandidateCount': 1},
                    ],
                },
            },
        },
    )

    assert (
        PlacementPhysicalAssembly
        .SelectCompletePhysicalEligibilityRepairTerminalPositions(
            Failure,
            'B1',
        )
        == frozenset(((11, 1, 37), (18, 1, 39)))
    )
    assert not (
        PlacementPhysicalAssembly
        .SelectCompletePhysicalEligibilityRepairTerminalPositions(
            Failure,
            'CarryIn',
        )
    )

    ChannelizedPlacement = SimpleNamespace(Placed=SimpleNamespace(
        PlacedGates=(
            SimpleNamespace(
                Name='B1Owner',
                Outputs=('B1',),
                OutputPin=(11, 1, 37),
                Inputs=('NandNet4',),
                InputPins=((9, 1, 37),),
            ),
            SimpleNamespace(
                Name='B1Consumer',
                Outputs=('NandNet8',),
                OutputPin=(20, 1, 39),
                Inputs=('B1',),
                InputPins=((18, 1, 39),),
            ),
        ),
    ))
    assert (
        PlacementPhysicalAssembly
        .SelectCompletePhysicalEligibilityRepairEndpointGateNames(
            Failure,
            'B1',
            ChannelizedPlacement,
        )
        == frozenset(('B1Owner', 'B1Consumer'))
    )

def test_complete_physical_repair_keeps_pending_local_sibling_first():
    LocalFirst = SimpleNamespace(PlacementFingerprint='local-first')
    UnrelatedLocal = SimpleNamespace(PlacementFingerprint='unrelated-local')
    Fallback = SimpleNamespace(PlacementFingerprint='fallback')
    Queue = [
        ('prepare-eligibility', 1, LocalFirst, 0, 0),
        ('prepare-eligibility', 2, UnrelatedLocal, 0, 0),
        ('prepare-eligibility', 3, Fallback, 0, 0),
    ]

    assert (
        PlacementPhysicalAssembly
        .SelectLocalFactorCandidateQueueInsertionIndex(
            Queue,
            {
                'local-first': 'B1',
                'unrelated-local': 'NandNet4',
            },
            'B1',
        )
        == 1
    )
    assert (
        PlacementPhysicalAssembly
        .SelectLocalFactorCandidateQueueInsertionIndex(
            Queue,
            {},
            'B1',
        )
        == 0
    )

def test_cycle_repair_keeps_only_immediate_sibling_group_first():
    Select = (
        PlacementPhysicalAssembly
        .SelectLocalFactorCycleSiblingQueueInsertionIndex
    )
    FirstSibling = SimpleNamespace(PlacementFingerprint='first-sibling')
    StaleSibling = SimpleNamespace(PlacementFingerprint='stale-sibling')
    Fallback = SimpleNamespace(PlacementFingerprint='fallback')
    Queue = [
        ('prepare-eligibility', 1, FirstSibling, 0, 0),
        ('prepare-eligibility', 2, StaleSibling, 0, 0),
        ('prepare-eligibility', 3, Fallback, 0, 0),
    ]
    Groups = {
        'first-sibling': 'current-group',
        'stale-sibling': 'stale-group',
    }

    assert Select(Queue, Groups, 'current-group') == 1
    assert Select(Queue, Groups, 'stale-group') == 0
    assert Select(Queue, Groups, '') == 0

    PersistentSibling = SimpleNamespace(
        PlacementFingerprint='persistent-sibling',
        JointPortfolioIdentityFingerprint='portfolio-1',
    )
    assert Select(
        [('prepare-eligibility', 1, PersistentSibling, 0, 0)],
        {},
        '',
        'portfolio-1',
    ) == 1

def test_cycle_repair_promotes_exact_sibling_after_access_core_rescore():
    TargetSibling = SimpleNamespace(
        PlacementFingerprint='target-sibling',
        JointPortfolioIdentityFingerprint='target-portfolio',
    )
    StaleSibling = SimpleNamespace(
        PlacementFingerprint='stale-sibling',
        JointPortfolioIdentityFingerprint='stale-portfolio',
    )
    Fallback = SimpleNamespace(
        PlacementFingerprint='fallback',
        JointPortfolioIdentityFingerprint='',
    )
    Queue = [
        ('prepare-eligibility', 1, StaleSibling, 0, 0),
        ('prepare-eligibility', 2, Fallback, 0, 0),
        ('prepare-eligibility', 3, TargetSibling, 0, 0),
    ]

    Count = PlacementPhysicalAssembly.PrioritizeLocalFactorCycleSiblings(
        Queue,
        {'target-sibling': 'target-group'},
        'target-group',
        'target-portfolio',
    )

    assert Count == 1
    assert [Entry[2].PlacementFingerprint for Entry in Queue] == [
        'target-sibling',
        'stale-sibling',
        'fallback',
    ]

def test_local_factor_lineage_falls_back_to_persistent_repair_recipe():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(
            Placed=SimpleNamespace(LocalRouteDiagnostics={
                '__PlacementRecipe__': {
                    'TransactionalRepairSignalHistory': [
                        ['B1', 'Generate1'],
                        ['Generate1'],
                        ['NandNet37'],
                    ],
                },
            }),
        ),
    )
    Select = PlacementPhysicalAssembly.SelectLocalFactorRepairSignalLineage

    assert Select(Candidate, ()) == ('Generate1', 'NandNet37')
    assert Select(
        Candidate,
        ('Generate1', 'Generate1', 'NandNet37'),
    ) == ('Generate1', 'Generate1', 'NandNet37')

def test_transactional_repair_footprint_measures_exact_changed_clusters():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(
            Placed=SimpleNamespace(LocalRouteDiagnostics={
                '__TransactionalClusterEndpointRepair__': {
                    'Clusters': {
                        '3': {'FinalWidth': 11, 'FinalDepth': 16},
                        '12': {'FinalWidth': 3, 'FinalDepth': 4},
                    },
                },
            }),
        ),
    )

    assert (
        PlacementPhysicalAssembly
        .MeasureTransactionalRepairClusterFootprint(Candidate)
        == (188, 2)
    )

def test_least_footprint_repair_is_bounded_to_transition_and_first_repeat():
    Select = (
        PlacementPhysicalAssembly.ShouldPreferLeastFootprintLocalRepair
    )

    assert not Select(False, ('Generate1',), 'NandNet37')
    assert not Select(True, (), 'NandNet37')
    assert not Select(True, ('Generate1',), 'Generate1')
    assert Select(True, ('Generate1',), 'NandNet37')
    assert Select(True, ('Generate1', 'NandNet37'), 'NandNet37')
    assert not Select(
        True,
        ('Generate1', 'NandNet37', 'NandNet37'),
        'NandNet37',
    )

def test_complete_physical_empty_bank_prefers_access_distinct_sibling():
    Priorities = {
        Fingerprint: (
            PlacementPhysicalAssembly
            .ClassifyCompletePhysicalEligibilityCandidatePriority(
                Fingerprint,
                {
                    'default': (
                        'singleton-local-factor-repair-transition-v1',
                        'B1',
                        0,
                        (2,),
                        ('B1',),
                    ),
                    'distinct': (
                        'singleton-local-factor-repair-transition-v1',
                        'B1',
                        3,
                        (2,),
                        ('B1',),
                    ),
                },
            )
        )
        for Fingerprint in ('default', 'distinct')
    }

    assert Priorities['distinct'] < Priorities['default']

def test_transactional_repair_queue_preserves_external_terminals_first():
    def Candidate(Fingerprint, InvalidatedSignals):
        return SimpleNamespace(
            PlacementFingerprint=Fingerprint,
            Placement=SimpleNamespace(
                Placed=SimpleNamespace(
                    LocalRouteDiagnostics={
                        '__TransactionalClusterEndpointRepair__': {
                            'InvalidatedSignals': InvalidatedSignals,
                        },
                    },
                ),
            ),
        )

    ExternalRepair = Candidate('external', ['A1', 'NandNet4'])
    InternalRepair = Candidate('internal', ['NandNet4', 'NandNet5'])

    Ordered = sorted(
        (ExternalRepair, InternalRepair),
        key=lambda Value: (
            PlacementPhysicalAssembly.BuildTransactionalRepairRoutingPriority(
                Value,
                ('A1', 'B1'),
            )
        ),
    )

    assert [Value.PlacementFingerprint for Value in Ordered] == [
        'internal',
        'external',
    ]

def test_complete_feedthrough_endpoint_domain_lifts_channel_repair_core():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentChannelCapacityUnsatisfiable,
        Stage="PhysicalComponentAssemblyPlanning",
        AffectedNets=("Transit",),
        Diagnostics={
            "FeedthroughCandidateDomainComplete": True,
            "ComponentFabricConstructionComplete": True,
            "OwnershipSearchComplete": True,
            "FeedthroughEndpointPrescreenComplete": True,
            "FeedthroughEndpointDomainFingerprint": "feedthrough-proof",
        },
    )

    Core = BuildPhysicalInterfaceRepairCore(Failure, Candidate)

    assert Core is not None
    assert Core.RepairLevel == "channel-capacity"
    assert Core.ProofKind == "complete-feedthrough-endpoint-domain"
    assert Core.Signals == ("Transit",)

def test_complete_capacity_pair_evidence_survives_feedback_escalation():
    Evidence = BuildSymbolicCapacityRepairEvidence(
        {
            "SymbolicCapacityProofFingerprint": "complete-pair-proof",
            "LocalCapacityCoreClause": [
                ["Beta", "seam-beta"],
                ["Alpha", "seam-alpha"],
            ],
        },
        ("Beta", "Alpha"),
    )
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(
            Placed=SimpleNamespace(
                LocalRouteClaims=(),
                PlacedGates=(
                    SimpleNamespace(
                        Name="GateA",
                        Inputs=("Alpha",),
                        InputPins=((1, 1, 1),),
                        Outputs=("OtherA",),
                        OutputPin=(1, 1, 2),
                    ),
                    SimpleNamespace(
                        Name="GateB",
                        Inputs=("Beta",),
                        InputPins=((2, 1, 1),),
                        Outputs=("OtherB",),
                        OutputPin=(2, 1, 2),
                    ),
                ),
            ),
            Clusters=(("Outside",), (), ("GateA",), (), ("GateB",)),
        ),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalSymbolicCapacityPlacementFeedback",
        Diagnostics={
            "SymbolicCapacityPlacementFeedback": True,
            "PlacementInterfacePressureSignals": ["Alpha", "Beta"],
            "SelectedComponentClusters": [4, 2],
            **Evidence,
        },
    )

    Constraint = BuildPhysicalInterfaceRepairCore(Failure, Candidate)

    assert Evidence == {
        "SymbolicCapacityProofComplete": True,
        "SymbolicCapacityProofFingerprint": "complete-pair-proof",
        "LocalCapacityCoreClause": [
            ["Alpha", "seam-alpha"],
            ["Beta", "seam-beta"],
        ],
    }
    assert Constraint is not None
    assert Constraint.RepairLevel == "local-assembly"
    assert Constraint.ProofKind == "complete-symbolic-capacity-core"
    assert Constraint.Signals == ("Alpha", "Beta")
    assert Constraint.ClusterIds == (2, 4)
    assert Constraint.ComponentGateNames == ("GateA", "GateB")
    assert Constraint.ForcedSeamClasses == (
        ("Alpha", "seam-alpha"),
        ("Beta", "seam-beta"),
    )
    assert BuildSymbolicCapacityRepairEvidence(
        {"SymbolicCapacityProofFingerprint": "incomplete"},
        ("Alpha", "Beta"),
    ) == {}

def test_capacity_repair_geometry_includes_pair_pin_positions():
    def CandidateAt(X):
        Gate = SimpleNamespace(
            Name="Producer",
            Outputs=("Alpha",),
            OutputPin=(X, 7, 0),
            Inputs=("Beta",),
            InputPins=((X, 7, 1),),
        )
        return SimpleNamespace(
            Placement=SimpleNamespace(Placed=SimpleNamespace(
                LocalRouteClaims=(),
                PlacedGates=(Gate,),
            )),
        )

    First = BuildCapacityRepairGeometryFingerprint(
        CandidateAt(1), ("Alpha", "Beta"),
    )
    Second = BuildCapacityRepairGeometryFingerprint(
        CandidateAt(2), ("Alpha", "Beta"),
    )

    assert First != Second

def test_owned_frontier_repair_expands_bounded_topology_equivalent_signals():
    Fingerprints = {
        "A1": "symmetric-input-bit-one",
        "B1": "symmetric-input-bit-one",
        "CarryIn": "carry-input",
    }

    assert PlacementPhysicalAssembly.SelectTopologyEquivalentRepairSignals(
        ("B1",),
        Fingerprints,
    ) == frozenset(("A1", "B1"))
    assert PlacementPhysicalAssembly.SelectTopologyEquivalentRepairSignals(
        ("B1",),
        Fingerprints,
        MaximumSignals=1,
    ) == frozenset(("B1",))

def test_owned_frontier_repair_domain_is_stable_across_symmetric_signals():
    Fingerprints = {
        "A1": "symmetric-input-bit-one",
        "B1": "symmetric-input-bit-one",
        "CarryIn": "carry-input",
    }

    First = (
        PlacementPhysicalAssembly
        .BuildOwnedFrontierTopologyRepairDomainFingerprint(
            ("A1",),
            Fingerprints,
        )
    )
    Second = (
        PlacementPhysicalAssembly
        .BuildOwnedFrontierTopologyRepairDomainFingerprint(
            ("B1",),
            Fingerprints,
        )
    )
    Carry = (
        PlacementPhysicalAssembly
        .BuildOwnedFrontierTopologyRepairDomainFingerprint(
            ("CarryIn",),
            Fingerprints,
        )
    )

    assert First == Second
    assert First != Carry

def test_incomplete_capacity_pair_cannot_build_repair_constraint():
    Candidate = SimpleNamespace(
        Placement=SimpleNamespace(Placed=SimpleNamespace(LocalRouteClaims=())),
    )
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.ComponentPortAssignmentUnsatisfiable,
        Stage="PhysicalSymbolicCapacityPlacementFeedback",
        Diagnostics={
            "SymbolicCapacityPlacementFeedback": True,
            "PlacementInterfacePressureSignals": ["Alpha", "Beta"],
            "LocalCapacityCoreClause": [
                ["Alpha", "seam-alpha"], ["Beta", "seam-beta"],
            ],
        },
    )

    assert BuildPhysicalInterfaceRepairCore(Failure, Candidate) is None

def test_incomplete_ownership_core_cannot_drive_feedback():
    Failure = RoutingFailure(
        Reason=RoutingFailureReason.RuntimeBudgetExceeded,
        Stage="PhysicalEligibilitySolveAfterUnarySupport",
        AffectedNets=("NandNet26",),
        Diagnostics={"OwnershipUnsatCoreFingerprint": "core"},
    )

    assert BuildComponentRoutabilityCore(
        Failure,
        PlacementStateFingerprint="placement",
        ComponentStateFingerprint="component",
        DomainFingerprint="domain",
        CoreFingerprint="core",
        Complete=False,
    ) is None

def test_complete_local_capacity_proof_crosses_frozen_interface_as_typed_placement_feedback():
    Context = SimpleNamespace(
        CumulativeSymbolicCapacityPressureSignals={'Generate1', 'CarryIn'},
        LatestSymbolicCapacityRepairEvidence={
            'SymbolicCapacityProofComplete': True,
            'SymbolicCapacityProofFingerprint': 'capacity-proof',
            'LocalCapacityCoreClause': [
                ['CarryIn', 'carry-seam'],
                ['Generate1', 'generate-seam'],
            ],
        },
        PreRouteInterfaceResult=SimpleNamespace(
            ToDictionary=lambda: {'SelectionFingerprint': 'frozen-interface'},
        ),
        SymbolicCapacityAssemblyReplanCount=0,
    )

    with pytest.raises(RoutingStageError) as Raised:
        PlacementPhysicalAssembly.ReplanPhysicalAssemblyWithTiming(Context)

    Failure = Raised.value.Failure
    assert Failure.Reason == RoutingFailureReason.ComponentPortAssignmentUnsatisfiable
    assert Failure.Stage == 'PhysicalSymbolicCapacityPlacementFeedback'
    assert Failure.AffectedNets == ('CarryIn', 'Generate1')
    assert Failure.Diagnostics['AutomaticReplanDisabled'] is True
    assert Failure.Diagnostics['SymbolicCapacityPlacementFeedback'] is True
    assert Failure.Diagnostics['PlacementInterfacePressureSignals'] == [
        'CarryIn',
        'Generate1',
    ]
    assert Failure.Diagnostics['GlobalPlanningEntered'] is False
    assert Failure.Diagnostics['LocalCompilationEntered'] is False

def test_capacity_repair_seam_witness_is_guidance_with_exact_fallback():
    assert PhysicalPortSearch.BuildCapacityRepairSeamRestrictionPasses(
        {},
        {"Alpha": "seam-a", "Beta": "seam-b"},
    ) == (
        {"Alpha": "seam-a", "Beta": "seam-b"},
        {},
    )
    assert PhysicalPortSearch.BuildCapacityRepairSeamRestrictionPasses(
        {"Alpha": "seam-a"},
        {"Alpha": "seam-a", "Beta": "seam-b"},
    ) == (
        {"Alpha": "seam-a", "Beta": "seam-b"},
        {"Alpha": "seam-a"},
    )

def test_capacity_repair_seam_witness_does_not_override_boundary_contract():
    assert PhysicalPortSearch.BuildCapacityRepairSeamRestrictionPasses(
        {"Alpha": "boundary-seam"},
        {"Alpha": "repair-seam"},
    ) == ({"Alpha": "boundary-seam"},)

def test_capacity_repair_seam_witness_projects_to_boundary_preferences():
    Preparation = SimpleNamespace(
        LocalAccessFactorsBySignal=(("Alpha", (
            SimpleNamespace(
                LocalAccessFingerprint="local-a",
                SeamContractFingerprint="seam-a",
            ),
            SimpleNamespace(
                LocalAccessFingerprint="local-other",
                SeamContractFingerprint="seam-other",
            ),
        )),),
        ApertureFactorsBySignal=(("Alpha", (
            SimpleNamespace(
                ApertureOptionFingerprint="option-z",
                GlobalContractFingerprint="global-z",
                ApertureContractFingerprint="aperture-z",
            ),
            SimpleNamespace(
                ApertureOptionFingerprint="option-a",
                GlobalContractFingerprint="global-a",
                ApertureContractFingerprint="aperture-a",
            ),
        )),),
        LocalApertureSupportBySignal=(("Alpha", (
            SimpleNamespace(
                LocalAccessFingerprint="local-a",
                ApertureOptionFingerprint="option-z",
            ),
            SimpleNamespace(
                LocalAccessFingerprint="local-a",
                ApertureOptionFingerprint="option-a",
            ),
            SimpleNamespace(
                LocalAccessFingerprint="local-other",
                ApertureOptionFingerprint="option-z",
            ),
        )),),
    )

    Global, Aperture = (
        PhysicalPortSearch.SelectCapacityRepairBoundaryPreferences(
            Preparation,
            {"Alpha": "seam-a", "Missing": "seam-missing"},
        )
    )

    assert Global == {"Alpha": "global-a"}
    assert Aperture == {"Alpha": "aperture-a"}
