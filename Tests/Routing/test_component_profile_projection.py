from dataclasses import replace
from types import SimpleNamespace

from Compiler.Routing.ChannelPlanner import NetRoutingProfile
from Compiler.Routing.Components.PhysicalPlanning import (
    ApplyPhysicalComponentAssemblyGlobalProfiles,
)
from Compiler.Routing.Contracts.Component import (
    PhysicalComponentAssemblyPlan,
    PhysicalComponentPortReservation,
)
from Compiler.Routing.ResourceGraph import RoutingResourceClaims


def _Claims(*Nodes):
    Cells = frozenset(Nodes)
    return RoutingResourceClaims(
        WireCells=Cells,
        SupportCells=frozenset(
            (X, Y - 1, Z) for X, Y, Z in Cells
        ),
        ElectricalCells=Cells,
    )


def _Profile(Signal, Root, Targets):
    return NetRoutingProfile(
        Signal=Signal,
        Root=Root,
        Targets=tuple(Targets),
        Span=max(
            (
                abs(Target[0] - Root[0])
                + abs(Target[2] - Root[2])
                for Target in Targets
            ),
            default=0,
        ),
        Fanout=len(Targets),
        RetryCount=2,
        Criticality=3,
        IsTrunk=len(Targets) > 1,
        SourceAccessPath=(Root, (Root[0] + 1, Root[1], Root[2])),
        TargetAccessPaths={
            Target: (Target, (Target[0] - 1, Target[1], Target[2]))
            for Target in Targets
        },
    )


def _Port(Signal, Direction, OwnedTerminals, Attachment, GlobalPath):
    return PhysicalComponentPortReservation(
        Signal=Signal,
        Direction=Direction,
        OwnedTerminals=tuple(OwnedTerminals),
        OwnedTerminalFingerprints=tuple(
            f"terminal:{Index}"
            for Index, _Terminal in enumerate(OwnedTerminals)
        ),
        OwnedCandidateFingerprints=(),
        FabricDomainFingerprint="fabric-domain",
        FabricAttachment=Attachment,
        Attachment=Attachment,
        LocalPath=(Attachment,),
        GlobalPath=tuple(GlobalPath),
        Claims=_Claims(*GlobalPath),
        ReservationFingerprint=f"port:{Direction}:{Attachment}",
    )


def _Plan(*Ports):
    return PhysicalComponentAssemblyPlan(
        PlanFingerprint="assembly-plan",
        PortAssignmentFingerprint="port-assignment",
        PlacementFingerprint="placement",
        ComponentGraphFingerprint="component-graph",
        ResourceGraphFingerprint="resource-graph",
        TechnologyFingerprint="technology",
        InterfaceFingerprint="interface",
        ComponentId=4,
        EnvelopeMinimum=(0, 0, 0),
        EnvelopeMaximum=(20, 10, 20),
        KeepoutClaims=_Claims(),
        Ports=tuple(Ports),
        Channels=(),
    )


def _Problem(ComponentSignals, TerminalsBySignal, Plan=None):
    return SimpleNamespace(
        ComponentSignals=tuple(ComponentSignals),
        OwnedTerminalDomains=tuple(
            SimpleNamespace(Signal=Signal, Terminal=Terminal)
            for Signal, Terminals in TerminalsBySignal.items()
            for Terminal in Terminals
        ),
        PhysicalAssemblyPlan=Plan,
    )


def test_internal_only_component_profile_disappears_before_global_routing():
    Root = (4, 1, 4)
    Targets = ((8, 1, 4), (8, 1, 8))
    Plan = _Plan()
    Problem = _Problem(
        ("Internal",),
        {"Internal": (Root, *Targets)},
        Plan,
    )

    Result = ApplyPhysicalComponentAssemblyGlobalProfiles(
        {"Internal": _Profile("Internal", Root, Targets)},
        Problem,
        Plan,
    )

    assert Result == {}


def test_covered_producer_root_uses_exact_reserved_global_port_path():
    CoveredRoot = (5, 1, 5)
    OutsideTargets = ((40, 1, 5), (44, 1, 9))
    Attachment = (20, 7, 5)
    GlobalPath = (Attachment, (23, 7, 5), (26, 7, 5))
    Port = _Port(
        "Exported",
        "output",
        (CoveredRoot,),
        Attachment,
        GlobalPath,
    )
    Plan = _Plan(Port)
    Original = _Profile("Exported", CoveredRoot, OutsideTargets)
    Problem = _Problem(
        ("Exported",),
        {"Exported": (CoveredRoot,)},
        Plan,
    )

    Result = ApplyPhysicalComponentAssemblyGlobalProfiles(
        {"Exported": Original},
        Problem,
        Plan,
    )["Exported"]

    assert Result.Root == Attachment
    assert Result.SourceAccessPath == GlobalPath
    assert Result.Targets == OutsideTargets
    assert Result.TargetAccessPaths == Original.TargetAccessPaths
    assert Result.Fanout == 2
    assert Result.Span == 28


def test_covered_sink_targets_collapse_to_one_exact_reserved_attachment():
    Root = (-12, 1, 6)
    CoveredTargets = ((4, 1, 4), (8, 1, 8))
    OutsideTarget = (40, 1, 12)
    Attachment = (0, 7, 6)
    GlobalPath = (Attachment, (-3, 7, 6), (-6, 7, 6))
    Port = _Port(
        "Imported",
        "input",
        CoveredTargets,
        Attachment,
        GlobalPath,
    )
    Plan = _Plan(Port)
    Original = _Profile(
        "Imported",
        Root,
        (*CoveredTargets, OutsideTarget),
    )
    Problem = _Problem(
        ("Imported",),
        {"Imported": CoveredTargets},
        Plan,
    )

    Result = ApplyPhysicalComponentAssemblyGlobalProfiles(
        {"Imported": Original},
        Problem,
        Plan,
    )["Imported"]

    assert Result.Root == Root
    assert Result.SourceAccessPath == Original.SourceAccessPath
    assert Result.Targets == (OutsideTarget, Attachment)
    assert Result.TargetAccessPaths == {
        OutsideTarget: Original.TargetAccessPaths[OutsideTarget],
        Attachment: GlobalPath,
    }
    assert Result.Fanout == 2
    assert Result.Span == 58


def test_outside_profile_is_preserved_without_rebuilding_access_contract():
    Root = (-30, 1, -4)
    Target = (-10, 1, -4)
    Outside = _Profile("Outside", Root, (Target,))
    Plan = _Plan()
    Problem = _Problem(("Internal",), {"Internal": ()}, Plan)

    Result = ApplyPhysicalComponentAssemblyGlobalProfiles(
        {"Outside": Outside},
        Problem,
        Plan,
    )

    assert Result["Outside"] is Outside


def test_profile_projection_is_signal_rename_and_input_order_invariant():
    CoveredRoot = (4, 1, 4)
    OutsideTarget = (36, 1, 4)
    Attachment = (16, 7, 4)
    GlobalPath = (Attachment, (19, 7, 4), (22, 7, 4))
    Outside = _Profile("Outside", (-20, 1, -8), ((-4, 1, -8),))

    def Project(ComponentSignal, ReverseOrder):
        Port = _Port(
            ComponentSignal,
            "output",
            (CoveredRoot,),
            Attachment,
            GlobalPath,
        )
        Plan = _Plan(Port)
        Component = _Profile(
            ComponentSignal,
            CoveredRoot,
            (OutsideTarget,),
        )
        Items = (
            (("Outside", Outside), (ComponentSignal, Component))
            if ReverseOrder
            else ((ComponentSignal, Component), ("Outside", Outside))
        )
        return ApplyPhysicalComponentAssemblyGlobalProfiles(
            dict(Items),
            _Problem(
                (ComponentSignal,),
                {ComponentSignal: (CoveredRoot,)},
                Plan,
            ),
            Plan,
        )

    Original = Project("Carry", False)
    RenamedReordered = Project("Renamed", True)

    assert Original["Outside"] == RenamedReordered["Outside"]
    assert replace(Original["Carry"], Signal="Normalized") == replace(
        RenamedReordered["Renamed"],
        Signal="Normalized",
    )
