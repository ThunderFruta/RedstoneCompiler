"""Behavior-neutral source-structure retirement and inventory checks."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
import re
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

MaximumImplementationModuleLines = 3_000
MaximumOrchestratorLines = 499
MaximumFunctionLines = 999
MinimumSplitImplementationModuleLines = 150

SplitImplementationRoots = (
    "Compiler/Placement/Access",
    "Compiler/Placement/Core",
    "Compiler/Placement/Flow",
    "Compiler/Routing/Authoritative",
    "Compiler/Routing/Components",
    "Compiler/Routing/Contracts",
    "Compiler/Routing/Interfaces",
    "RustRouting/Src",
)

# These files are deliberately small because they are APIs, state/schema
# boundaries, process-global identity owners, or single ordered phase
# boundaries.  A new short file must be added here with a concrete reason; this
# prevents helper fragmentation from silently recreating the monolith problem.
DocumentedShortBoundaryExceptions = {
    "Compiler/Placement/Core/Cache.py": "process-global cache identity owner",
    "Compiler/Placement/Core/Commit.py": "public placement API and orchestrator",
    "Compiler/Placement/Core/CommitState.py": "placement commit state contract",
    "Compiler/Placement/Flow/Runner.py": "public placement-flow orchestrator",
    "Compiler/Placement/Flow/State.py": "placement flow state and services contract",
    "Compiler/Routing/Authoritative/BoundaryLeaseDomains.py": "boundary-lease domain boundary",
    "Compiler/Routing/Authoritative/BoundaryLeaseState.py": "boundary-lease state contract",
    "Compiler/Routing/Authoritative/BoundaryLeases.py": "boundary-lease public facade",
    "Compiler/Routing/Authoritative/Dependencies.py": "call-time service binding boundary",
    "Compiler/Routing/Authoritative/Flow.py": "public authoritative orchestrator",
    "Compiler/Routing/Authoritative/Materialization.py": "materialization phase boundary",
    "Compiler/Routing/Authoritative/NegotiatedTrees.py": "negotiated-tree public facade",
    "Compiler/Routing/Authoritative/PortPreparation.py": "physical-port preparation facade",
    "Compiler/Routing/Authoritative/PortPreparationState.py": "physical-port preparation state contract",
    "Compiler/Routing/Interfaces/PhysicalClaims.py": "neutral physical-claim interface boundary",
    "RustRouting/Src/Core/Deadline.rs": "native deadline contract boundary",
    "RustRouting/Src/Core/Runtime.rs": "native thread-pool ownership boundary",
}

OrchestratorPaths = (
    "Compiler/Placement/Core/Commit.py",
    "Compiler/Placement/Flow/Runner.py",
    "Compiler/Routing/Authoritative/BoundaryLeases.py",
    "Compiler/Routing/Authoritative/Flow.py",
    "Compiler/Routing/Authoritative/NegotiatedTrees.py",
    "Compiler/Routing/Authoritative/PortPreparation.py",
    "Compiler/Routing/Authoritative/PortSolving/__init__.py",
    "Compiler/Routing/Components/Pipeline.py",
    "RustRouting/Src/Lib.rs",
)

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


def PhysicalLineCount(SourcePath: Path) -> int:
    """Count physical source lines using the repository's structural metric."""
    return len(SourcePath.read_text(encoding="utf-8").splitlines())


def IterPythonFunctionSpans(
    SourcePath: Path,
) -> tuple[tuple[str, int, int], ...]:
    """Return qualified Python function names, starts, and physical spans."""
    Root = ast.parse(
        SourcePath.read_text(encoding="utf-8"),
        filename=str(SourcePath),
    )
    Result: list[tuple[str, int, int]] = []

    def Visit(Node: ast.AST, Owners: tuple[str, ...] = ()) -> None:
        for Child in ast.iter_child_nodes(Node):
            if isinstance(Child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                ChildOwners = (*Owners, Child.name)
                if isinstance(Child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    EndLine = Child.end_lineno or Child.lineno
                    Result.append((
                        ".".join(ChildOwners),
                        Child.lineno,
                        EndLine - Child.lineno + 1,
                    ))
                Visit(Child, ChildOwners)
            else:
                Visit(Child, Owners)

    Visit(Root)
    return tuple(Result)


def ScrubRustCommentsAndLiterals(Source: str) -> str:
    """Blank Rust comments and literals while preserving offsets/newlines."""
    Result = list(Source)
    Length = len(Source)
    Index = 0

    def Blank(Start: int, End: int) -> None:
        for CharacterIndex in range(Start, End):
            if Result[CharacterIndex] != "\n":
                Result[CharacterIndex] = " "

    while Index < Length:
        if Source.startswith("//", Index):
            End = Source.find("\n", Index)
            End = Length if End < 0 else End
            Blank(Index, End)
            Index = End
            continue
        if Source.startswith("/*", Index):
            Start = Index
            Depth = 1
            Index += 2
            while Index < Length and Depth:
                if Source.startswith("/*", Index):
                    Depth += 1
                    Index += 2
                elif Source.startswith("*/", Index):
                    Depth -= 1
                    Index += 2
                else:
                    Index += 1
            Blank(Start, Index)
            continue

        RawMatch = re.match(r'(?:br|cr|r)(#+)?"', Source[Index:])
        if RawMatch is not None:
            Start = Index
            Hashes = RawMatch.group(1) or ""
            Index += len(RawMatch.group(0))
            Terminator = f'"{Hashes}'
            End = Source.find(Terminator, Index)
            Index = Length if End < 0 else End + len(Terminator)
            Blank(Start, Index)
            continue

        if Source[Index] == '"':
            Start = Index
            Index += 1
            while Index < Length:
                if Source[Index] == "\\":
                    Index += 2
                elif Source[Index] == '"':
                    Index += 1
                    break
                else:
                    Index += 1
            Blank(Start, min(Index, Length))
            continue

        if Source[Index] == "'":
            # Lifetimes have no closing quote; bounded lookahead distinguishes
            # them from character literals without needing a full Rust parser.
            End = Index + 1
            Escaped = False
            SearchLimit = min(Length, Index + 20)
            while End < SearchLimit and Source[End] != "\n":
                if not Escaped and Source[End] == "'":
                    break
                if Source[End] == "\\" and not Escaped:
                    Escaped = True
                else:
                    Escaped = False
                End += 1
            if End < SearchLimit and Source[End] == "'":
                Blank(Index, End + 1)
                Index = End + 1
                continue
        Index += 1
    return "".join(Result)


def IterRustFunctionSpans(
    SourcePath: Path,
) -> tuple[tuple[str, int, int], ...]:
    """Return native function names, starts, and brace-balanced spans."""
    Source = SourcePath.read_text(encoding="utf-8")
    Scrubbed = ScrubRustCommentsAndLiterals(Source)
    Result: list[tuple[str, int, int]] = []
    Pattern = re.compile(r"\bfn\s+([A-Za-z_]\w*)[^;{]*\{", re.DOTALL)
    for Match in Pattern.finditer(Scrubbed):
        OpeningBrace = Match.end() - 1
        Depth = 0
        ClosingBrace: int | None = None
        for Index in range(OpeningBrace, len(Scrubbed)):
            if Scrubbed[Index] == "{":
                Depth += 1
            elif Scrubbed[Index] == "}":
                Depth -= 1
                if Depth == 0:
                    ClosingBrace = Index
                    break
        if ClosingBrace is None:
            raise AssertionError(
                f"unterminated Rust function {Match.group(1)} in {SourcePath}"
            )
        StartLine = Scrubbed.count("\n", 0, Match.start()) + 1
        EndLine = Scrubbed.count("\n", 0, ClosingBrace) + 1
        Result.append((Match.group(1), StartLine, EndLine - StartLine + 1))
    return tuple(Result)


def IterRustMacroSpans(
    SourcePath: Path,
) -> tuple[tuple[str, int, int], ...]:
    """Return macro_rules names, starts, and brace-balanced spans."""
    Source = SourcePath.read_text(encoding="utf-8")
    Scrubbed = ScrubRustCommentsAndLiterals(Source)
    Result: list[tuple[str, int, int]] = []
    Pattern = re.compile(r"\bmacro_rules!\s*([A-Za-z_]\w*)\s*\{")
    for Match in Pattern.finditer(Scrubbed):
        OpeningBrace = Match.end() - 1
        Depth = 0
        ClosingBrace: int | None = None
        for Index in range(OpeningBrace, len(Scrubbed)):
            if Scrubbed[Index] == "{":
                Depth += 1
            elif Scrubbed[Index] == "}":
                Depth -= 1
                if Depth == 0:
                    ClosingBrace = Index
                    break
        if ClosingBrace is None:
            raise AssertionError(
                f"unterminated Rust macro {Match.group(1)} in {SourcePath}"
            )
        StartLine = Scrubbed.count("\n", 0, Match.start()) + 1
        EndLine = Scrubbed.count("\n", 0, ClosingBrace) + 1
        Result.append((Match.group(1), StartLine, EndLine - StartLine + 1))
    return tuple(Result)


def IsSplitImplementationPath(SourcePath: Path) -> bool:
    """Return whether a source belongs to the clean-break package tree."""
    RelativePath = SourcePath.relative_to(RepositoryRoot).as_posix()
    return any(
        RelativePath == Root or RelativePath.startswith(f"{Root}/")
        for Root in SplitImplementationRoots
    )


def HasAutomaticShortBoundaryException(SourcePath: Path) -> bool:
    """Recognize package APIs, bindings, and neutral contract modules."""
    RelativePath = SourcePath.relative_to(RepositoryRoot).as_posix()
    return bool(
        SourcePath.name in {"__init__.py", "mod.rs"}
        or RelativePath == "RustRouting/Src/Lib.rs"
        or "/Contracts/" in RelativePath
        or "/Python/" in RelativePath
        or "/Workers/" in RelativePath
    )


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

    def testImplementationModulesStayWithinPhysicalLineCeiling(self) -> None:
        Oversized: list[tuple[str, int]] = []
        for SourcePath in sorted(CompilerRoot.rglob("*.py")):
            if SourcePath.name == "__init__.py":
                continue
            LineCount = PhysicalLineCount(SourcePath)
            if LineCount > MaximumImplementationModuleLines:
                Oversized.append((
                    SourcePath.relative_to(RepositoryRoot).as_posix(),
                    LineCount,
                ))
        for SourcePath in sorted(RustSourceRoot.rglob("*.rs")):
            if SourcePath.name == "mod.rs" or SourcePath.name == "Lib.rs":
                continue
            LineCount = PhysicalLineCount(SourcePath)
            if LineCount > MaximumImplementationModuleLines:
                Oversized.append((
                    SourcePath.relative_to(RepositoryRoot).as_posix(),
                    LineCount,
                ))

        self.assertEqual(tuple(Oversized), ())

    def testOrchestratorsStayBelowFiveHundredPhysicalLines(self) -> None:
        Violations = tuple(
            (RelativePath, PhysicalLineCount(RepositoryRoot / RelativePath))
            for RelativePath in OrchestratorPaths
            if PhysicalLineCount(RepositoryRoot / RelativePath)
            > MaximumOrchestratorLines
        )

        self.assertEqual(Violations, ())

    def testPythonAndRustFunctionsAndPhaseMacrosStayBelowOneThousandLines(
        self,
    ) -> None:
        Violations: list[tuple[str, str, int, int]] = []
        for SourcePath in sorted(CompilerRoot.rglob("*.py")):
            for Name, StartLine, Span in IterPythonFunctionSpans(SourcePath):
                if Span > MaximumFunctionLines:
                    Violations.append((
                        SourcePath.relative_to(RepositoryRoot).as_posix(),
                        Name,
                        StartLine,
                        Span,
                    ))
        for SourcePath in sorted(RustSourceRoot.rglob("*.rs")):
            for Name, StartLine, Span in IterRustFunctionSpans(SourcePath):
                if Span > MaximumFunctionLines:
                    Violations.append((
                        SourcePath.relative_to(RepositoryRoot).as_posix(),
                        Name,
                        StartLine,
                        Span,
                    ))
            for Name, StartLine, Span in IterRustMacroSpans(SourcePath):
                if Span > MaximumFunctionLines:
                    Violations.append((
                        SourcePath.relative_to(RepositoryRoot).as_posix(),
                        f"macro_rules! {Name}",
                        StartLine,
                        Span,
                    ))

        self.assertEqual(tuple(Violations), ())

    def testNewShortModulesAreExplicitBoundaries(self) -> None:
        Undocumented: list[tuple[str, int]] = []
        for SourcePath in sorted((
            *CompilerRoot.rglob("*.py"),
            *RustSourceRoot.rglob("*.rs"),
        )):
            if not IsSplitImplementationPath(SourcePath):
                continue
            LineCount = PhysicalLineCount(SourcePath)
            if LineCount >= MinimumSplitImplementationModuleLines:
                continue
            RelativePath = SourcePath.relative_to(RepositoryRoot).as_posix()
            if (
                HasAutomaticShortBoundaryException(SourcePath)
                or RelativePath in DocumentedShortBoundaryExceptions
            ):
                continue
            Undocumented.append((RelativePath, LineCount))

        StaleExceptions = tuple(
            RelativePath
            for RelativePath in sorted(DocumentedShortBoundaryExceptions)
            if not (RepositoryRoot / RelativePath).is_file()
        )

        self.assertEqual((tuple(Undocumented), StaleExceptions), ((), ()))

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
