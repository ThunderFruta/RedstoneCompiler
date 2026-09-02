"""Behavior-neutral source-structure retirement and inventory checks."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
import unittest


RepositoryRoot = Path(__file__).resolve().parents[2]
CompilerRoot = RepositoryRoot / "Compiler"
RustSourceRoot = RepositoryRoot / "RustRouting/Src"

BannedModulePaths = (
    "Compiler/Cells/Nand.py",
    "Compiler/Simulation/Redstone.py",
    "Compiler/Simulation/__init__.py",
    "Compiler/Placement/AccessFabric.py",
    "Compiler/Placement/Pcb.py",
    "Compiler/Placement/PcbFlow.py",
    "Compiler/Routing/Actions/ConflictRepair.py",
    "Compiler/Routing/AuthoritativePlanner.py",
    "Compiler/Routing/ComponentAccess.py",
    "Compiler/Routing/ComponentPipeline.py",
    "Compiler/Routing/ComponentPlanning.py",
    "Compiler/Routing/ComponentRouter.py",
    "Compiler/Routing/Models.py",
    "RustRouting/Src/Assignment.rs",
    "RustRouting/Src/AssignmentPlanning.rs",
    "RustRouting/Src/Bindings.rs",
    "RustRouting/Src/Deadline.rs",
    "RustRouting/Src/EscapePlanning.rs",
    "RustRouting/Src/Generation.rs",
    "RustRouting/Src/LeasePlanning.rs",
    "RustRouting/Src/Models.rs",
    "RustRouting/Src/PathRouting.rs",
    "RustRouting/Src/Simulation/LogicSimulation.rs",
    "RustRouting/Src/Simulation/mod.rs",
)
BannedModuleNames = frozenset({
    "Compiler.Cells.Nand",
    "Compiler.Simulation",
    "Compiler.Simulation.Redstone",
    "Compiler.Placement.AccessFabric",
    "Compiler.Placement.Pcb",
    "Compiler.Placement.PcbFlow",
    "Compiler.Routing.Actions.ConflictRepair",
    "Compiler.Routing.AuthoritativePlanner",
    "Compiler.Routing.ComponentAccess",
    "Compiler.Routing.ComponentPipeline",
    "Compiler.Routing.ComponentPlanning",
    "Compiler.Routing.ComponentRouter",
    "Compiler.Routing.Models",
})

LowerLayerPrefixes = {
    "Compiler.Routing.Contracts": (
        "Compiler.Routing.Interfaces",
        "Compiler.Routing.Components",
        "Compiler.Routing.Authoritative",
        "Compiler.Placement",
    ),
    "Compiler.Routing.Interfaces": (
        "Compiler.Routing.Components",
        "Compiler.Routing.Authoritative",
        "Compiler.Placement",
    ),
    "Compiler.Routing.Components": (
        "Compiler.Routing.Authoritative",
        "Compiler.Placement",
    ),
    "Compiler.Routing.Authoritative": ("Compiler.Placement",),
}

# Geometry and rotation are intentionally neutral physical primitives retained
# in their established namespace; importing them does not couple authoritative
# routing to placement search or placement-flow orchestration.
DocumentedDependencyExceptions = frozenset({
    (
        "Compiler.Routing.Authoritative.CandidateCache",
        "Compiler.Placement.Geometry",
    ),
    (
        "Compiler.Routing.Authoritative.Dependencies",
        "Compiler.Placement.Rotation",
    ),
})


def BuildPythonModuleIndex(
    SourceRoot: Path,
) -> dict[str, tuple[Path, bool]]:
    """Map importable source names to paths and package-module status."""
    Result: dict[str, tuple[Path, bool]] = {}
    for SourcePath in sorted(SourceRoot.rglob("*.py")):
        RelativePath = SourcePath.relative_to(RepositoryRoot)
        Parts = list(RelativePath.with_suffix("").parts)
        IsPackage = Parts[-1] == "__init__"
        if IsPackage:
            Parts.pop()
        ModuleName = ".".join(Parts)
        Result[ModuleName] = (SourcePath, IsPackage)
    return Result


def ResolveImportedModules(
    CurrentModule: str,
    CurrentIsPackage: bool,
    ImportNode: ast.Import | ast.ImportFrom,
    KnownModules: frozenset[str],
) -> frozenset[str]:
    """Resolve one static import to known repository module identities."""
    Resolved: set[str] = set()
    if isinstance(ImportNode, ast.Import):
        for Alias in ImportNode.names:
            if Alias.name in KnownModules:
                Resolved.add(Alias.name)
        return frozenset(Resolved)

    if ImportNode.level:
        PackageParts = CurrentModule.split(".")
        if not CurrentIsPackage:
            PackageParts.pop()
        AscendCount = ImportNode.level - 1
        if AscendCount > len(PackageParts):
            return frozenset()
        if AscendCount:
            PackageParts = PackageParts[:-AscendCount]
        if ImportNode.module:
            PackageParts.extend(ImportNode.module.split("."))
        BaseModule = ".".join(PackageParts)
    else:
        BaseModule = ImportNode.module or ""

    if BaseModule in KnownModules:
        Resolved.add(BaseModule)
    for Alias in ImportNode.names:
        if Alias.name == "*":
            continue
        Candidate = ".".join(
            Value for Value in (BaseModule, Alias.name) if Value
        )
        if Candidate in KnownModules:
            Resolved.add(Candidate)
    return frozenset(Resolved)


def BuildCompilerImportGraph(
    SourceRoot: Path = CompilerRoot,
    AdditionalKnownModules: frozenset[str] = frozenset(),
) -> dict[str, frozenset[str]]:
    """Build a deterministic static import graph for compiler modules."""
    ModuleIndex = BuildPythonModuleIndex(SourceRoot)
    KnownModules = frozenset({*ModuleIndex, *AdditionalKnownModules})
    Result: dict[str, frozenset[str]] = {}
    for ModuleName, (SourcePath, IsPackage) in sorted(ModuleIndex.items()):
        RootNode = ast.parse(
            SourcePath.read_text(encoding="utf-8"),
            filename=str(SourcePath),
        )
        Dependencies: set[str] = set()
        for Node in ast.walk(RootNode):
            if isinstance(Node, (ast.Import, ast.ImportFrom)):
                Dependencies.update(ResolveImportedModules(
                    ModuleName,
                    IsPackage,
                    Node,
                    KnownModules,
                ))
        Result[ModuleName] = frozenset(Dependencies)
    return Result


def FindImportCycles(
    Graph: dict[str, frozenset[str] | set[str]],
) -> tuple[tuple[str, ...], ...]:
    """Return deterministic strongly connected import components."""
    Nodes = frozenset({
        *Graph,
        *(Dependency for Values in Graph.values() for Dependency in Values),
    })
    NextIndex = 0
    Indices: dict[str, int] = {}
    LowLinks: dict[str, int] = {}
    Pending: list[str] = []
    PendingSet: set[str] = set()
    Components: list[tuple[str, ...]] = []

    def Visit(ModuleName: str) -> None:
        nonlocal NextIndex
        Indices[ModuleName] = NextIndex
        LowLinks[ModuleName] = NextIndex
        NextIndex += 1
        Pending.append(ModuleName)
        PendingSet.add(ModuleName)

        for Dependency in sorted(Graph.get(ModuleName, frozenset())):
            if Dependency not in Indices:
                Visit(Dependency)
                LowLinks[ModuleName] = min(
                    LowLinks[ModuleName],
                    LowLinks[Dependency],
                )
            elif Dependency in PendingSet:
                LowLinks[ModuleName] = min(
                    LowLinks[ModuleName],
                    Indices[Dependency],
                )

        if LowLinks[ModuleName] != Indices[ModuleName]:
            return
        Component: list[str] = []
        while True:
            Current = Pending.pop()
            PendingSet.remove(Current)
            Component.append(Current)
            if Current == ModuleName:
                break
        OrderedComponent = tuple(sorted(Component))
        if len(OrderedComponent) > 1 or ModuleName in Graph.get(
            ModuleName,
            frozenset(),
        ):
            Components.append(OrderedComponent)

    for ModuleName in sorted(Nodes):
        if ModuleName not in Indices:
            Visit(ModuleName)
    return tuple(sorted(Components))


class SourceStructureTests(unittest.TestCase):
    def testRetiredModulePathsStayDeleted(self) -> None:
        Existing = tuple(
            RelativePath
            for RelativePath in BannedModulePaths
            if (
                (RepositoryRoot / RelativePath).exists()
                or (RepositoryRoot / RelativePath).is_symlink()
            )
        )

        self.assertEqual(Existing, ())

    def testCompilerSourcesDoNotImportRetiredModules(self) -> None:
        Graph = BuildCompilerImportGraph(
            AdditionalKnownModules=BannedModuleNames,
        )
        References = tuple(sorted(
            (ModuleName, Dependency)
            for ModuleName, Dependencies in Graph.items()
            for Dependency in Dependencies
            if Dependency in BannedModuleNames
        ))

        self.assertEqual(References, ())

    def testRepositoryPythonDoesNotReferenceRetiredImportNames(self) -> None:
        References: list[tuple[str, str]] = []
        for SourceRoot in (
            RepositoryRoot / "Compiler",
            RepositoryRoot / "Scripts",
            RepositoryRoot / "Tests",
        ):
            for SourcePath in sorted(SourceRoot.rglob("*.py")):
                if SourcePath == Path(__file__).resolve():
                    continue
                Source = SourcePath.read_text(encoding="utf-8")
                for ModuleName in sorted(BannedModuleNames):
                    if ModuleName in Source:
                        References.append((
                            SourcePath.relative_to(RepositoryRoot).as_posix(),
                            ModuleName,
                        ))

        self.assertEqual(tuple(References), ())

    def testRoutingDependencyLayersStayOneWay(self) -> None:
        Graph = BuildCompilerImportGraph()
        Violations: list[tuple[str, str]] = []
        for LowerPrefix, ForbiddenPrefixes in LowerLayerPrefixes.items():
            for ModuleName, Dependencies in Graph.items():
                if not (
                    ModuleName == LowerPrefix
                    or ModuleName.startswith(f"{LowerPrefix}.")
                ):
                    continue
                for Dependency in Dependencies:
                    if any(
                        Dependency == Prefix
                        or Dependency.startswith(f"{Prefix}.")
                        for Prefix in ForbiddenPrefixes
                    ):
                        Pair = (ModuleName, Dependency)
                        if Pair not in DocumentedDependencyExceptions:
                            Violations.append(Pair)

        self.assertEqual(tuple(sorted(Violations)), ())

    def testNarrowPublicEntrypointsHaveConcreteOwners(self) -> None:
        from Compiler.Placement.Access import BuildPlacementAccessFabric
        from Compiler.Placement.Core import PlacePcbGraph
        from Compiler.Placement.Flow import PlaceAndRoutePcb
        from Compiler.Routing.Authoritative import RouteAuthoritativeResources
        from Compiler.Routing.Components import (
            CompileClosedComponent,
            SolveComponentRoutingProblem,
        )

        Owners = {
            BuildPlacementAccessFabric: "Compiler.Placement.Access.Fabric",
            PlacePcbGraph: "Compiler.Placement.Core.Commit",
            PlaceAndRoutePcb: "Compiler.Placement.Flow.Runner",
            RouteAuthoritativeResources: "Compiler.Routing.Authoritative.Flow",
            CompileClosedComponent: "Compiler.Routing.Components.Pipeline",
            SolveComponentRoutingProblem: "Compiler.Routing.Components.Solver",
        }
        self.assertEqual(
            tuple(sorted(
                (Function.__name__, Function.__module__, ExpectedOwner)
                for Function, ExpectedOwner in Owners.items()
                if Function.__module__ != ExpectedOwner
            )),
            (),
        )
        for Function in Owners:
            self.assertIsInstance(inspect.signature(Function), inspect.Signature)

    def testAuthoritativePhaseRunnerAcceptsExplicitServices(self) -> None:
        from Compiler.Routing.Authoritative.Flow import (
            RunAuthoritativeRoutingPhases,
        )
        from Compiler.Routing.Authoritative.RunState import (
            AuthoritativeRoutingServices,
            AuthoritativeRoutingState,
            PhaseOutcome,
        )

        Sentinel = object()
        Services = AuthoritativeRoutingServices({"Sentinel": Sentinel})

        def RunInjectedPhase(State, ReceivedServices):
            self.assertIsInstance(State, AuthoritativeRoutingState)
            self.assertIs(ReceivedServices.Sentinel, Sentinel)
            return PhaseOutcome(Returned=True, Value="injected-result")

        self.assertEqual(
            RunAuthoritativeRoutingPhases(
                AuthoritativeRoutingState(),
                Services,
                Phases=(RunInjectedPhase,),
            ),
            "injected-result",
        )

    def testActionsPackagePreservesConsolidatedConflictExports(self) -> None:
        from Compiler.Routing import Actions
        from Compiler.Routing.Actions import Validation

        self.assertIs(
            Actions.AnalyzeFlatRouteConflicts,
            Validation.AnalyzeFlatRouteConflicts,
        )
        self.assertIs(
            Actions.FindFlatRouteConflicts,
            Validation.FindFlatRouteConflicts,
        )
        self.assertIn("AnalyzeFlatRouteConflicts", Actions.__all__)
        self.assertIn("FindFlatRouteConflicts", Actions.__all__)

    def testImportCycleDetectorFindsStronglyConnectedComponents(self) -> None:
        Graph = {
            "Alpha": frozenset({"Beta"}),
            "Beta": frozenset({"Gamma"}),
            "Gamma": frozenset({"Alpha", "Leaf"}),
            "Leaf": frozenset(),
            "Self": frozenset({"Self"}),
        }

        self.assertEqual(
            FindImportCycles(Graph),
            (("Alpha", "Beta", "Gamma"), ("Self",)),
        )

    def testCompilerImportsAreAcyclic(self) -> None:
        self.assertEqual(
            FindImportCycles(BuildCompilerImportGraph()),
            (),
        )


if __name__ == "__main__":
    unittest.main()
