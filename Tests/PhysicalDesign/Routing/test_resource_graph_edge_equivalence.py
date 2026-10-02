"""Region-edge equivalence from an exhaustive ordered public-primitive oracle."""

from itertools import combinations, permutations, product

import pytest

from PhysicalDesign.Resources.ResourceGraph import RoutingResourceGraph


def BuildGraph(*, Blocked=False, GraphType=RoutingResourceGraph):
    Occupied = {(1, 1, 0)}
    if Blocked:
        Occupied.add((0, 2, 0))
    return GraphType(
        ActualBlocks=frozenset(Occupied),
        ElectricalBlocks=frozenset(),
        SolidBlocks=frozenset(Occupied),
    )


def Subsets(Values):
    Values = tuple(Values)
    return tuple(
        frozenset(Selection)
        for Size in range(len(Values) + 1)
        for Selection in combinations(Values, Size)
    )


def Oracle(Graph, Bounds, Columns, Access):
    """Enumerate coordinates and both orientations without region helpers."""
    X0, X1, Y0, Y1, Z0, Z1 = Bounds
    Nodes = frozenset(
        (X, Y, Z)
        for X, Y, Z in product(
            range(X0, X1 + 1), range(Y0, Y1 + 1), range(Z0, Z1 + 1)
        )
        if (Columns is None or (X, Z) in Columns)
        and Graph.IsLegalNode((X, Y, Z), Access)
    )
    Edges = frozenset(
        tuple(sorted((First, Second)))
        for First, Second in permutations(sorted(Nodes), 2)
        if Graph.BuildPrimitive(First, Second) is not None
    )
    return Nodes, Edges


def AssertRegion(Graph, Bounds, Columns=None, Access=frozenset()):
    ExpectedNodes, ExpectedEdges = Oracle(Graph, Bounds, Columns, Access)
    Region = Graph.BuildRegion(Bounds, AllowedColumns=Columns, AllowedAccess=Access)
    assert Region.Nodes == ExpectedNodes
    assert Region.Edges == ExpectedEdges
    assert all(First < Second for First, Second in Region.Edges)
    return Region


@pytest.mark.parametrize("Blocked", (False, True), ids=("clear", "blocked"))
def test_every_small_column_and_access_subset_matches_ordered_primitive_union(Blocked):
    Bounds = (0, 1, 1, 2, 0, 1)
    Columns = ((0, 0), (0, 1), (1, 0), (1, 1))
    AccessNodes = ((0, 1, 0), (1, 2, 0))
    Requests = (
        (ColumnSet, Access)
        for ColumnSet in Subsets(Columns)
        for Access in Subsets(
            Position for Position in AccessNodes
            if (Position[0], Position[2]) in ColumnSet
        )
    )
    # Scope: access lies inside the requested columns and bounds. The public
    # contract does not resolve precedence for access outside those columns.
    for ColumnSet, Access in Requests:
        # A fresh graph prevents a cache hit from masking the construction oracle.
        Graph = BuildGraph(Blocked=Blocked)
        Region = AssertRegion(Graph, Bounds, ColumnSet, Access)
        Reordered = AssertRegion(
            BuildGraph(Blocked=Blocked), Bounds,
            frozenset(reversed(sorted(ColumnSet))),
            frozenset(reversed(sorted(Access))),
        )
        assert Region == Reordered
        assert AssertRegion(Graph, Bounds, ColumnSet, Access) == Region


@pytest.mark.parametrize("Direction", ((1, 0), (-1, 0), (0, 1), (0, -1)))
@pytest.mark.parametrize("Blocked", (False, True), ids=("clear", "blocked"))
def test_literal_stair_edge_survives_region_normalization_in_each_direction(Direction, Blocked):
    X, Z = Direction
    Lower, Upper = (0, 1, 0), (X, 2, Z)
    Supports = {(0, 0, 0), (X, 1, Z)}
    Occupied = Supports | ({(0, 2, 0)} if Blocked else set())
    Graph = RoutingResourceGraph(
        ActualBlocks=frozenset(Occupied), ElectricalBlocks=frozenset(),
        SolidBlocks=frozenset(Occupied),
    )
    Bounds = (min(0, X), max(0, X), 1, 2, min(0, Z), max(0, Z))
    Access = frozenset({Lower, Upper})
    Region = AssertRegion(Graph, Bounds, frozenset({(0, 0), (X, Z)}), Access)
    Edge = tuple(sorted((Lower, Upper)))
    assert Lower in Region.Nodes and Upper in Region.Nodes
    assert (Edge in Region.Edges) is (not Blocked)
    for First, Second in ((Lower, Upper), (Upper, Lower)):
        assert (Graph.BuildPrimitive(First, Second) is not None) is (not Blocked)


class ReverseOnlyGraph(RoutingResourceGraph):
    """Public primitive collaborator with a directional acceptance contract."""

    def BuildPrimitive(self, First, Second):
        if First < Second:
            return None
        return super().BuildPrimitive(First, Second)

    def CanBuildNeighborPrimitive(self, First, Second):
        return First > Second and super().CanBuildNeighborPrimitive(First, Second)


def test_rejected_orientation_does_not_suppress_reverse_legal_edge():
    Graph = ReverseOnlyGraph(frozenset(), frozenset(), frozenset())
    Lower, Upper = (0, 1, 0), (1, 2, 0)
    assert Graph.BuildPrimitive(Lower, Upper) is None
    assert Graph.BuildPrimitive(Upper, Lower) is not None
    Region = AssertRegion(Graph, (0, 1, 1, 2, 0, 0))
    assert (Lower, Upper) in Region.Edges
    assert ((0, 1, 0), (1, 1, 0)) in Region.Edges
    assert ((0, 1, 0), (0, 2, 0)) not in Region.Edges
    assert Graph.BuildPrimitive((0, 1, 0), (2, 1, 0)) is None


@pytest.mark.parametrize("Blocked", (False, True), ids=("clear", "blocked"))
def test_height_columns_and_changed_access_sequence_matches_fresh_ordered_oracle(Blocked):
    Graph = BuildGraph(Blocked=Blocked)
    Bounds = (0, 1, 1, 2, 0, 1)
    Columns = frozenset({(0, 0), (1, 0), (0, 1), (1, 1)})
    Access = frozenset({(0, 1, 0), (1, 2, 0)})
    Requests = (
        ((0, 1, 1, 1, 0, 1), frozenset({(0, 0)}), frozenset()),
        (Bounds, frozenset({(0, 0)}), frozenset()),
        (Bounds, Columns, frozenset()),
        (Bounds, Columns, Access),
        (Bounds, Columns, frozenset({(0, 1, 0)})),
        (Bounds, Columns, frozenset()),
    )
    CachedNodes, CachedEdges = set(), set()
    for RequestBounds, RequestColumns, RequestAccess in Requests:
        Region = AssertRegion(Graph, RequestBounds, RequestColumns, RequestAccess)
        Fresh = AssertRegion(
            BuildGraph(Blocked=Blocked), RequestBounds, RequestColumns, RequestAccess
        )
        assert Region == Fresh
        CachedNodes.update(Region.Nodes)
        CachedEdges.update(Region.Edges)
        assert Graph.CachedNodeCount == len(CachedNodes)
        assert Graph.CachedEdgeCount == len(CachedEdges)


@pytest.mark.parametrize("Phase", ("nodes", "edges", "complete"))
@pytest.mark.parametrize("SeedCache", (False, True), ids=("empty", "seeded"))
@pytest.mark.parametrize("Reason", ("deadline", "cancelled"))
def test_interrupted_region_keeps_completed_cache_union_and_retry_is_complete(Phase, SeedCache, Reason):
    Graph = BuildGraph()
    if SeedCache:
        AssertRegion(Graph, (0, 1, 1, 1, 0, 0))
    Before = Graph.CachedNodeCount, Graph.CachedEdgeCount
    Interruption = TimeoutError(Reason) if Reason == "deadline" else RuntimeError(Reason)
    Observed = []

    def WorkCheck(Diagnostics):
        Observed.append(Diagnostics["Phase"])
        if Diagnostics["Phase"] == Phase:
            raise Interruption

    Bounds = (0, 2, 1, 2, 0, 1)
    with pytest.raises(type(Interruption)) as Raised:
        Graph.BuildRegion(Bounds, WorkCheck=WorkCheck)
    assert Raised.value is Interruption
    assert Phase in Observed
    if Phase != "complete":
        assert "complete" not in Observed
    assert (Graph.CachedNodeCount, Graph.CachedEdgeCount) == Before
    Retried = AssertRegion(Graph, Bounds)
    Fresh = AssertRegion(BuildGraph(), Bounds)
    assert Retried == Fresh
    assert Graph.CachedNodeCount == len(Retried.Nodes)
    assert Graph.CachedEdgeCount == len(Retried.Edges)
