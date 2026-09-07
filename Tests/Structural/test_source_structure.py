"""Behavior-neutral source-structure retirement and inventory checks."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


RepositoryRoot = Path(__file__).resolve().parents[2]
CompilerRoot = RepositoryRoot / "Compiler"
CompilerSiblingRoots = (
    RepositoryRoot / "App",
    RepositoryRoot / "PhysicalDesign",
    RepositoryRoot / "Validation",
)
RustSourceRoot = RepositoryRoot / "Kernels/Routing/Src"

BannedModulePaths = (
    "Compilation/Cells/Nand.py",
    "Compilation/Placement/AccessFabric.py",
    "Compilation/Placement/Pcb.py",
    "Compilation/Placement/PcbFlow.py",
    "Compilation/Routing/Actions/ConflictRepair.py",
    "Compilation/Routing/AuthoritativePlanner.py",
    "Compilation/Routing/ComponentAccess.py",
    "Compilation/Routing/ComponentPipeline.py",
    "Compilation/Routing/ComponentPlanning.py",
    "Compilation/Routing/ComponentRouter.py",
    "Compilation/Routing/Models.py",
    "Compilation/Simulation/Redstone.py",
    "Compilation/Simulation/__init__.py",
    "Kernels/Routing/Src/Assignment.rs",
    "Kernels/Routing/Src/AssignmentPlanning.rs",
    "Kernels/Routing/Src/Bindings.rs",
    "Kernels/Routing/Src/Deadline.rs",
    "Kernels/Routing/Src/EscapePlanning.rs",
    "Kernels/Routing/Src/Generation.rs",
    "Kernels/Routing/Src/LeasePlanning.rs",
    "Kernels/Routing/Src/Models.rs",
    "Kernels/Routing/Src/PathRouting.rs",
    "Kernels/Routing/Src/Simulation/LogicSimulation.rs",
    "Kernels/Routing/Src/Simulation/mod.rs",
    "PhysicalDesign/Cells/Nand.py",
    "PhysicalDesign/Placement/AccessFabric.py",
    "PhysicalDesign/Placement/Pcb.py",
    "PhysicalDesign/Placement/PcbFlow.py",
    "PhysicalDesign/Redstone/Rules/ConflictRepair.py",
    "PhysicalDesign/Routing/AuthoritativePlanner.py",
    "PhysicalDesign/Routing/ComponentAccess.py",
    "PhysicalDesign/Routing/ComponentPipeline.py",
    "PhysicalDesign/Routing/ComponentPlanning.py",
    "PhysicalDesign/Routing/ComponentRouter.py",
    "PhysicalDesign/Routing/Models.py",
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
    "Compilation.Cells.Nand",
    "Compilation.Placement.AccessFabric",
    "Compilation.Placement.Pcb",
    "Compilation.Placement.PcbFlow",
    "Compilation.Routing.Actions.ConflictRepair",
    "Compilation.Routing.AuthoritativePlanner",
    "Compilation.Routing.ComponentAccess",
    "Compilation.Routing.ComponentPipeline",
    "Compilation.Routing.ComponentPlanning",
    "Compilation.Routing.ComponentRouter",
    "Compilation.Routing.Models",
    "Compilation.Simulation",
    "Compilation.Simulation.Redstone",
    "PhysicalDesign.Cells.Nand",
    "PhysicalDesign.Placement.AccessFabric",
    "PhysicalDesign.Placement.Pcb",
    "PhysicalDesign.Placement.PcbFlow",
    "PhysicalDesign.Redstone.Rules.ConflictRepair",
    "PhysicalDesign.Routing.AuthoritativePlanner",
    "PhysicalDesign.Routing.ComponentAccess",
    "PhysicalDesign.Routing.ComponentPipeline",
    "PhysicalDesign.Routing.ComponentPlanning",
    "PhysicalDesign.Routing.ComponentRouter",
    "PhysicalDesign.Routing.Models",
})

LowerLayerPrefixes = {
    "PhysicalDesign.Contracts": (
        "PhysicalDesign.Constraints",
        "PhysicalDesign.Routing.Regions",
        "PhysicalDesign.Routing.Global",
        "PhysicalDesign.Placement",
        "PhysicalDesign.Orchestration",
        "PhysicalDesign.Geometry",
    ),
    "PhysicalDesign.Constraints": (
        "PhysicalDesign.Routing.Regions",
        "PhysicalDesign.Routing.Global",
        "PhysicalDesign.Placement",
        "PhysicalDesign.Orchestration",
        "PhysicalDesign.Geometry",
    ),
    "PhysicalDesign.Routing.Regions": (
        "PhysicalDesign.Routing.Global",
        "PhysicalDesign.Placement",
        "PhysicalDesign.Orchestration",
        "PhysicalDesign.Geometry",
    ),
    "PhysicalDesign.Routing.Global": (
        "PhysicalDesign.Placement",
        "PhysicalDesign.Orchestration",
        "PhysicalDesign.Geometry",
    ),
}

# Preserve the two existing geometry exceptions across the namespace move.
# Flow and geometry retain the restrictions of their former placement owner.
DocumentedDependencyExceptions = frozenset({
    (
        "PhysicalDesign.Routing.Global.Candidates.CandidateCache",
        "PhysicalDesign.Geometry.Placement",
    ),
    (
        "PhysicalDesign.Routing.Global.Orchestration.Dependencies",
        "PhysicalDesign.Geometry.Rotation",
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
    if SourceRoot == CompilerRoot:
        for SiblingRoot in CompilerSiblingRoots:
            ModuleIndex.update(BuildPythonModuleIndex(SiblingRoot))
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
        from PhysicalDesign.Placement.Access import BuildPlacementAccessFabric
        from PhysicalDesign.Placement.Engine import PlacePcbGraph
        from PhysicalDesign.Orchestration import PlaceAndRoutePcb
        from PhysicalDesign.Routing.Global import RouteAuthoritativeResources
        from PhysicalDesign.Routing.Regions import CompileClosedComponent, SolveComponentRoutingProblem

        Owners = {
            BuildPlacementAccessFabric: "PhysicalDesign.Placement.Access.Fabric",
            PlacePcbGraph: "PhysicalDesign.Placement.Engine.Construction.Commit",
            PlaceAndRoutePcb: "PhysicalDesign.Orchestration.Runner",
            RouteAuthoritativeResources: "PhysicalDesign.Routing.Global.Orchestration.Flow",
            CompileClosedComponent: "PhysicalDesign.Routing.Regions.Pipeline",
            SolveComponentRoutingProblem: "PhysicalDesign.Routing.Regions.Solving.Solver",
        }
        self.assertEqual(
            tuple(sorted(
                (Function.__name__, Function.__module__, ExpectedOwner)
                for Function, ExpectedOwner in Owners.items()
                if Function.__module__ != ExpectedOwner
            )),
            (),
        )

    def testActionsPackagePreservesConsolidatedConflictExports(self) -> None:
        from PhysicalDesign.Redstone import Rules
        from PhysicalDesign.Redstone.Rules import Validation

        self.assertIs(
            Rules.AnalyzeFlatRouteConflicts,
            Validation.AnalyzeFlatRouteConflicts,
        )
        self.assertIs(
            Rules.FindFlatRouteConflicts,
            Validation.FindFlatRouteConflicts,
        )
        self.assertIn("AnalyzeFlatRouteConflicts", Rules.__all__)
        self.assertIn("FindFlatRouteConflicts", Rules.__all__)

    def testCompilerImportsAreAcyclic(self) -> None:
        self.assertEqual(
            FindImportCycles(BuildCompilerImportGraph()),
            (),
        )


if __name__ == "__main__":
    unittest.main()
