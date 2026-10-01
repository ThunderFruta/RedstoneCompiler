"""Independent claim-set oracles for first-decisive rejection capture."""

from collections import Counter
from dataclasses import FrozenInstanceError
import json
from types import SimpleNamespace

import pytest

from PhysicalDesign.Contracts.Core import RoutingStaticGeometry
from PhysicalDesign.Contracts.Results import RoutingResources
from PhysicalDesign.Redstone.Technology import DefaultRedstoneRoutingTechnology
from PhysicalDesign.Resources.ResourceGraph import (
    PinAccessPortal, RoutingResourceClaims, RoutingResourceGraph,
    RoutingResourceId, RoutingResourceKind,
)
from PhysicalDesign.Routing.Global.Ports import Portals
from PhysicalDesign.Routing.Global.Ports.MaterializationEvidence import (
    CaptureMaterializationSelfConflict,
)


def Resource(Kind, Position):
    return RoutingResourceId(RoutingResourceKind(Kind), Position)


@pytest.mark.parametrize('Kind,Roles', (
    ('Support', ('Support', 'Air')),
    ('Support', ('Support', 'Wire')),
    ('Air', ('Air', 'Wire')),
))
def test_literal_same_signal_conflict_retains_both_claim_memberships(Kind, Roles):
    Position = (-2, 1, 0)
    Claims = RoutingResourceClaims(
        WireCells=frozenset({Position}) if 'Wire' in Roles else frozenset({(-2, 0, 0)}),
        SupportCells=frozenset({Position}) if 'Support' in Roles else frozenset(),
        RequiredAirCells=frozenset({Position}) if 'Air' in Roles else frozenset(),
    )
    Evidence = CaptureMaterializationSelfConflict(
        'signal', {(-2, 0, 0), (-2, 2, 0)}, Claims,
        {Resource(Kind, Position): ('signal',)},
    )
    Document = Evidence.ToDictionary()
    assert Document['CaptureStatus'] == 'Complete'
    assert Document['Conflicts'] == [{
        'Kind': Kind, 'Position': [-2, 1, 0], 'ClaimKinds': list(Roles),
    }]
    assert Document['Nodes'] == [[-2, 0, 0], [-2, 2, 0]]
    assert Document['ContributorProvenance'] == 'Unavailable'
    assert Document['EvaluationIdentity'] == 'NotCaptured'
    assert json.loads(json.dumps(Document)) == Document


def test_multiple_claims_and_resource_counts_are_not_collapsed_by_owner():
    Position = (2, 1, 0)
    Claims = RoutingResourceClaims(*(frozenset({Position}) for _ in range(3)))
    Conflicts = {
        Resource('Support', Position): ('s',),
        Resource('Air', Position): ('s',),
    }
    First = CaptureMaterializationSelfConflict('s', {Position}, Claims, Conflicts)
    Second = CaptureMaterializationSelfConflict(
        's', {Position}, Claims, dict(reversed(tuple(Conflicts.items()))),
    )
    assert First == Second
    Document = First.ToDictionary()
    assert Document['ObservedConflictCount'] == 2
    assert Document['RetainedConflictCount'] == 2
    assert Document['Conflicts'] == [
        {'Kind': 'Air', 'Position': [2, 1, 0], 'ClaimKinds': ['Air', 'Wire']},
        {'Kind': 'Support', 'Position': [2, 1, 0],
         'ClaimKinds': ['Support', 'Wire', 'Air']},
    ]


def test_capture_and_dictionary_projection_own_their_data():
    Position = (2, 1, 0)
    Nodes = {(2, 0, 0)}
    Air = {Position}
    Claims = RoutingResourceClaims(
        SupportCells=frozenset({Position}), RequiredAirCells=Air,
    )
    Conflicts = {Resource('Support', Position): ('s',)}
    Evidence = CaptureMaterializationSelfConflict('s', Nodes, Claims, Conflicts)
    Expected = Evidence.ToDictionary()
    Nodes.clear()
    Air.clear()
    Conflicts.clear()
    Changed = Evidence.ToDictionary()
    Changed['Claims']['Air'].clear()
    Changed['Conflicts'][0]['ClaimKinds'].clear()
    assert Evidence.ToDictionary() == Expected
    with pytest.raises(FrozenInstanceError):
        Evidence.Signal = 'changed'


@pytest.mark.parametrize('Count,Retained,Coverage,Status,Omissions', (
    (16, 16, 'Complete', 'Complete', []),
    (17, 16, 'Partial', 'Partial', ['ConflictRetentionLimit']),
    (256, 16, 'Partial', 'Partial', ['ClaimCellLimit', 'ConflictRetentionLimit']),
    (257, 0, 'Unavailable', 'Partial', ['ClaimCellLimit', 'ConflictInspectionLimit']),
))
def test_retention_and_inspection_limits_have_exact_boundaries(
    Count, Retained, Coverage, Status, Omissions,
):
    Cells = frozenset((X, 1, 0) for X in range(Count))
    Claims = RoutingResourceClaims(SupportCells=Cells, RequiredAirCells=Cells)
    Conflicts = {Resource('Support', P): ('s',) for P in reversed(sorted(Cells))}
    Document = CaptureMaterializationSelfConflict('s', set(), Claims, Conflicts).ToDictionary()
    assert Document['ObservedConflictCount'] == Count
    assert Document['RetainedConflictCount'] == Retained
    assert Document['ConflictCoverage'] == Coverage
    assert Document['CaptureStatus'] == Status
    assert Document['Omissions'] == Omissions
    assert [V['Position'] for V in Document['Conflicts']] == [[X, 1, 0] for X in range(Retained)]


class NoTraversalSet(set):
    def __iter__(self):
        raise AssertionError('oversized snapshot traversed')


class NoTraversalDict(dict):
    def __iter__(self):
        raise AssertionError('oversized conflicts traversed')


def test_oversized_inputs_are_counted_without_traversal():
    Cells = NoTraversalSet((X, 1, 0) for X in range(257))
    Claims = RoutingResourceClaims(SupportCells=Cells, RequiredAirCells=Cells)
    Nodes = NoTraversalSet((X, 0, 0) for X in range(65))
    Conflicts = NoTraversalDict({Resource('Support', (X, 1, 0)): ('s',) for X in range(257)})
    Document = CaptureMaterializationSelfConflict('s', Nodes, Claims, Conflicts).ToDictionary()
    assert Document['ObservedConflictCount'] == 257
    assert Document['RetainedConflictCount'] == 0
    assert Document['ConflictCoverage'] == 'Unavailable'
    assert Document['Claims'] is None
    assert Document['Nodes'] is None
    assert Document['NodeCount'] == 65
    assert Document['ClaimCounts']['Support'] == 257
    assert Document['Omissions'] == ['ClaimCellLimit', 'ConflictInspectionLimit', 'NodeLimit']


def test_exact_node_and_claim_cell_limits_capture_the_complete_inputs():
    Cells = frozenset((X, 1, 0) for X in range(128))
    Nodes = {(X, 0, 0) for X in range(64)}
    Claims = RoutingResourceClaims(SupportCells=Cells, RequiredAirCells=Cells)
    Document = CaptureMaterializationSelfConflict(
        's', Nodes, Claims, {Resource('Support', P): ('s',) for P in Cells},
    ).ToDictionary()
    assert len(Document['Nodes']) == 64
    assert sum(len(V) for V in Document['Claims'].values()) == 256
    assert Document['Omissions'] == ['ConflictRetentionLimit']


def test_unobserved_or_contradictory_rejections_are_not_fabricated():
    Claims = RoutingResourceClaims(SupportCells=frozenset({(0, 0, 0)}))
    with pytest.raises(ValueError):
        CaptureMaterializationSelfConflict('s', set(), Claims, {})
    with pytest.raises(ValueError):
        CaptureMaterializationSelfConflict(
            's', set(), Claims, {Resource('Support', (0, 0, 0)): ('s',)},
        )
    with pytest.raises(ValueError):
        CaptureMaterializationSelfConflict(
            's', set(), Claims, {Resource('Support', (0, 0, 0)): ('other',)},
        )


def Materialize(Nodes, Diagnostics, Counts=None):
    Graph = RoutingResourceGraph(
        ActualBlocks=frozenset(), ElectricalBlocks=frozenset(), SolidBlocks=frozenset(),
    )
    Root = (0, 1, 0)
    Profile = SimpleNamespace(
        Seed=None, SourceAccessPath=(Root,), Targets=(), TargetAccessPaths={}, Root=Root,
    )
    Portal = PinAccessPortal(
        'source', 's', Root, 1, (Root,), frozenset(),
        RoutingResourceClaims(), 0, 0, 0, 0,
    )
    return Portals._MaterializeCandidate(
        's', Profile, Portal, (), frozenset(), 1, 'X', 0, 0,
        Nodes, None,
        RoutingResources(RoutingStaticGeometry(frozenset(), frozenset()), ResourceGraph=Graph),
        DefaultRedstoneRoutingTechnology, 1,
        RejectionCounts=Counts, MaterializationDiagnostics=Diagnostics,
    )


def test_real_stair_conflict_is_captured_by_its_single_decisive_evaluation(monkeypatch):
    # A stair from (0,1,0) to (1,2,0) needs air at (0,2,0).
    # The wire at (0,3,0) needs support in exactly that same cell.
    Nodes = [(0, 1, 0), (1, 2, 0), (0, 3, 0)]
    Original = Portals.FindSelfClaimConflicts
    Evaluations = []
    def Observe(Claims):
        Evaluations.append(Claims)
        assert len(Evaluations) == 1
        return Original(Claims)
    monkeypatch.setattr(Portals, 'FindSelfClaimConflicts', Observe)
    Diagnostics = {}
    Counts = Counter()
    assert Materialize(Nodes, Diagnostics, Counts) is None
    assert Diagnostics['Status'] == 'self-claim-conflict'
    assert Counts == {'SelfClaimConflict': 1}
    assert Diagnostics['ConflictCount'] == 1
    Evidence = Diagnostics['SelfClaimConflictEvidence']
    assert Evidence['Conflicts'] == [{
        'Kind': 'Support', 'Position': [0, 2, 0], 'ClaimKinds': ['Support', 'Air'],
    }]
    assert Evidence['Nodes'] == [[0, 1, 0], [0, 3, 0], [1, 2, 0]]
    assert [0, 2, 0] in Evidence['Claims']['Air']
    assert [0, 2, 0] in Evidence['Claims']['Support']


def test_no_tree_does_not_manufacture_physical_evidence():
    Diagnostics = {'SelfClaimConflictEvidence': {'Signal': 'previous-attempt'}}
    assert Materialize(None, Diagnostics) is None
    assert Diagnostics == {'Status': 'no-routed-tree'}


def test_successful_materialization_clears_stale_rejection_evidence():
    Diagnostics = {'SelfClaimConflictEvidence': {'Signal': 'previous-attempt'}}
    Candidate = Materialize([(0, 1, 0), (1, 1, 0)], Diagnostics)
    assert Candidate is not None
    assert Diagnostics['Status'] == 'accepted'
    assert 'SelfClaimConflictEvidence' not in Diagnostics


def test_disabled_diagnostics_do_not_capture_rejected_geometry(monkeypatch):
    Calls = []
    def Forbidden(*Args):
        Calls.append(Args)
        raise AssertionError('capture without a diagnostic consumer')
    monkeypatch.setattr(Portals, 'CaptureMaterializationSelfConflict', Forbidden)
    assert Materialize([(0, 1, 0), (1, 2, 0), (0, 3, 0)], None) is None
    assert Calls == []


def test_capture_error_preserves_rejection_but_does_not_swallow_cancellation(monkeypatch):
    def Broken(*Args):
        raise ValueError('diagnostic failure')
    monkeypatch.setattr(Portals, 'CaptureMaterializationSelfConflict', Broken)
    Diagnostics = {}
    assert Materialize([(0, 1, 0), (1, 2, 0), (0, 3, 0)], Diagnostics) is None
    assert Diagnostics['Status'] == 'self-claim-conflict'
    assert Diagnostics['ConflictCount'] == 1
    assert Diagnostics['SelfClaimConflictEvidence']['CaptureStatus'] == 'Unavailable'
    assert Diagnostics['SelfClaimConflictEvidence']['Omissions'] == ['CaptureError']
    def Cancelled(*Args):
        raise KeyboardInterrupt
    monkeypatch.setattr(Portals, 'CaptureMaterializationSelfConflict', Cancelled)
    with pytest.raises(KeyboardInterrupt):
        Materialize([(0, 1, 0), (1, 2, 0), (0, 3, 0)], {})
