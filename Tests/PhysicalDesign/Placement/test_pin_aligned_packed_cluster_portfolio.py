"""Focused contracts for the bounded pin-aligned graph-core portfolio."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from Compilation.Ir.Models import Gate, GateKind
from PhysicalDesign.Cells import Library as CellLibrary
from PhysicalDesign.Geometry.Placement import BuildPlacedGate
from PhysicalDesign.Placement.Engine import Cache as PlacementCache
from PhysicalDesign.Placement.Engine import Clustering as ClusteringModule
from PhysicalDesign.Placement.Engine import Compactness as CompactnessModule
from PhysicalDesign.Placement.Engine.Cache import (
    BuildOrientedGeometryPortfolioCacheIdentity,
    EagerCurrentCellGeometryResolver,
    OrientedCellGeometryCacheContext,
    OrientedGeometryCacheBuildReceipt,
)
from PhysicalDesign.Placement.Engine.Clustering import PcbGatesConflict
from PhysicalDesign.Placement.Engine.Compactness import (
    BuildPinAlignedPackedCluster,
    BuildPinAlignedPackedClusterPortfolio,
    CountPinAlignedPackedClusterPortfolio,
)
from PhysicalDesign.Redstone.Rules import Geometry as GeometryRules
from PhysicalDesign.Redstone.Technology import (
    DefaultRedstoneRoutingTechnology,
    RedstoneRoutingTechnology,
)


class PinAlignedPackedClusterPortfolioTests(unittest.TestCase):
    def setUp(self) -> None:
        PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()

    @staticmethod
    def ClearLegacyConflictHelpers() -> None:
        ClusteringModule._PhysicalGateGeometry.cache_clear()
        ClusteringModule._PhysicalGateElectricalExclusions.cache_clear()
        ClusteringModule._PhysicalGateAccessSignals.cache_clear()

    @staticmethod
    def BuildFixture() -> tuple[tuple[str, ...], dict[str, Gate]]:
        Gates = (
            Gate("N0", GateKind.NAND, ["S0"], ["A", "B"]),
            Gate("N1", GateKind.NAND, ["S1"], ["S0", "A"]),
            Gate("N2", GateKind.NAND, ["Result"], ["S1", "B"]),
        )
        InternalByName = {GateValue.Name: GateValue for GateValue in Gates}
        return tuple(InternalByName), InternalByName

    @staticmethod
    def BuildReconvergentFixture() -> tuple[
        tuple[str, ...],
        dict[str, Gate],
    ]:
        Gates = (
            Gate("N0", GateKind.NAND, ["N0"], ["A", "B"]),
            Gate("N1", GateKind.NAND, ["N1"], ["A", "C"]),
            Gate("Y", GateKind.NAND, ["Y"], ["N0", "N1"]),
        )
        InternalByName = {GateValue.Name: GateValue for GateValue in Gates}
        return tuple(InternalByName), InternalByName

    @staticmethod
    def BuildCellGate(Kind: GateKind) -> Gate:
        if Kind == GateKind.INPUT:
            return Gate("InputA", Kind, ["A"], [])
        if Kind == GateKind.NAND:
            return Gate("Nand0", Kind, ["Y"], ["A", "B"])
        return Gate("OutputY", Kind, [], ["Y"])

    def assertGeometryMatchesEager(
        self,
        Resolved: object,
        Eager: tuple[set[tuple[int, int, int]], ...],
        Technology: object = DefaultRedstoneRoutingTechnology,
    ) -> None:
        Actual, Electrical, Solid, ExplicitKeepOut = Eager
        self.assertEqual(Resolved.ActualBlocks, frozenset(Actual))
        self.assertEqual(Resolved.ElectricalBlocks, frozenset(Electrical))
        self.assertEqual(Resolved.SolidBlocks, frozenset(Solid))
        self.assertEqual(
            Resolved.ExplicitKeepOut,
            frozenset(ExplicitKeepOut),
        )
        self.assertEqual(
            Resolved.ElectricalExclusions,
            frozenset(
                Technology.BuildElectricalExclusions(set(Electrical))
                | ExplicitKeepOut
            ),
        )

    def assertPortfoliosExactlyEqual(
        self,
        First: object,
        Second: object,
    ) -> None:
        self.assertEqual(First, Second)
        self.assertEqual(First.States, Second.States)
        self.assertEqual(
            First.RawCandidateCount,
            Second.RawCandidateCount,
        )
        self.assertEqual(
            tuple(State.Materialize() for State in First.States),
            tuple(State.Materialize() for State in Second.States),
        )

    def assertGlobalPortfolioIdentityMutationRebuilds(
        self,
        MutationFactory: object,
    ) -> None:
        Names, InternalByName = self.BuildFixture()
        Baseline = BuildPinAlignedPackedClusterPortfolio(
            Names,
            InternalByName,
            BeamWidth=8,
        )
        BaselineEntries = dict(
            PlacementCache._PinAlignedPackedClusterPortfolioCache
        )
        try:
            with MutationFactory():
                Changed = BuildPinAlignedPackedClusterPortfolio(
                    Names,
                    InternalByName,
                    BeamWidth=8,
                )
                self.assertFalse(Changed.BuildReceipt.PortfolioCacheHit)
                self.assertGreater(Changed.BuildReceipt.RequestCount, 0)
                PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()
                CurrentEager = BuildPinAlignedPackedClusterPortfolio(
                    Names,
                    InternalByName,
                    BeamWidth=8,
                    UseOrientedGeometryCache=False,
                )
                self.assertFalse(
                    CurrentEager.BuildReceipt.PortfolioCacheHit
                )
                self.assertPortfoliosExactlyEqual(Changed, CurrentEager)
        finally:
            PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()
            PlacementCache._PinAlignedPackedClusterPortfolioCache.update(
                BaselineEntries
            )

        RestoredHit = BuildPinAlignedPackedClusterPortfolio(
            Names,
            InternalByName,
            BeamWidth=8,
        )
        self.assertPortfoliosExactlyEqual(RestoredHit, Baseline)
        self.assertTrue(RestoredHit.BuildReceipt.PortfolioCacheHit)
        self.assertEqual(RestoredHit.BuildReceipt.RequestCount, 0)
        self.assertEqual(RestoredHit.BuildReceipt.ProducerCallCount, 0)
        self.assertEqual(RestoredHit.BuildReceipt.MaximumEntryCount, 0)

    def testOrientedMasksMatchEagerAcrossFullDomainAndTranslations(
        self,
    ) -> None:
        OriginalProducer = (
            GeometryRules.BuildPlacedCellGeometryWithKeepOut
        )
        ProducerCalls = 0

        def ObserveProducer(Placed: object) -> tuple[set, set, set, set]:
            nonlocal ProducerCalls
            ProducerCalls += 1
            return OriginalProducer(Placed)

        Context = OrientedCellGeometryCacheContext(
            DefaultRedstoneRoutingTechnology
        )
        Origins = ((13, 4, -17), (-29, -3, 31), (47, 8, 59))
        SawExplicitKeepOut = False
        with patch.object(
            GeometryRules,
            "BuildPlacedCellGeometryWithKeepOut",
            side_effect=ObserveProducer,
        ):
            for Kind in (GateKind.INPUT, GateKind.NAND, GateKind.OUTPUT):
                Mirrors = (False, True) if Kind == GateKind.NAND else (False,)
                for Rotation in (0, 90, 180, 270):
                    for MirrorX in Mirrors:
                        for Origin in Origins:
                            GateValue = BuildPlacedGate(
                                self.BuildCellGate(Kind),
                                *Origin,
                                Rotation,
                                MirrorX,
                            )
                            Resolved = Context.Resolve(
                                GateValue,
                                DefaultRedstoneRoutingTechnology,
                            )
                            Eager = OriginalProducer(SimpleNamespace(
                                PlacedGates=[GateValue]
                            ))
                            self.assertGeometryMatchesEager(Resolved, Eager)
                            self.assertTrue(all(
                                isinstance(Mask, frozenset)
                                for Mask in (
                                    Resolved.ActualBlocks,
                                    Resolved.ElectricalBlocks,
                                    Resolved.SolidBlocks,
                                    Resolved.ExplicitKeepOut,
                                )
                            ))
                            SawExplicitKeepOut = (
                                SawExplicitKeepOut
                                or bool(Resolved.ExplicitKeepOut)
                            )

        Receipt = Context.BuildReceipt()
        self.assertTrue(SawExplicitKeepOut)
        self.assertEqual(Receipt.RequestCount, 48)
        self.assertEqual(Receipt.CacheMissCount, 16)
        self.assertEqual(Receipt.CacheHitCount, 32)
        self.assertEqual(Receipt.EagerFallbackCount, 0)
        self.assertEqual(Receipt.ProducerCallCount, 16)
        self.assertEqual(ProducerCalls, Receipt.ProducerCallCount)
        self.assertEqual(Receipt.AvoidedGeometryExpansionCount, 32)
        self.assertEqual(Receipt.TranslationMaterializationCount, 48)
        self.assertEqual(Receipt.RetainedEntryCount, 16)
        self.assertEqual(Receipt.MaximumEntryCount, 16)
        with self.assertRaises(AttributeError):
            Resolved.ActualBlocks.add((0, 0, 0))

    def testChangedProducerContextFallsBackEagerlyWithoutCacheHit(
        self,
    ) -> None:
        OriginalProducer = (
            GeometryRules.BuildPlacedCellGeometryWithKeepOut
        )
        Templates = GeometryRules.LoadRoutingTemplates()
        BaseGate = BuildPlacedGate(
            self.BuildCellGate(GateKind.NAND),
            7,
            2,
            -11,
            90,
            True,
        )
        TranslatedGate = BuildPlacedGate(
            self.BuildCellGate(GateKind.NAND),
            -19,
            5,
            23,
            90,
            True,
        )

        Context = OrientedCellGeometryCacheContext(
            DefaultRedstoneRoutingTechnology
        )
        Context.Resolve(BaseGate, DefaultRedstoneRoutingTechnology)
        with patch.object(
            GeometryRules,
            "LoadRoutingTemplates",
            return_value=dict(Templates),
        ):
            Resolved = Context.Resolve(
                TranslatedGate,
                DefaultRedstoneRoutingTechnology,
            )
            self.assertGeometryMatchesEager(
                Resolved,
                OriginalProducer(SimpleNamespace(
                    PlacedGates=[TranslatedGate]
                )),
            )
        self.assertEqual(
            Context.BuildReceipt().EagerFallbackReasons,
            (("ContextIdentityChanged", 1),),
        )

        ChangedTemplates = deepcopy(Templates)
        ChangedPosition = next(iter(ChangedTemplates["NAND"].Blocks))
        ChangedTemplates["NAND"].Blocks[ChangedPosition] = {
            "Name": "minecraft:air"
        }
        Context = OrientedCellGeometryCacheContext(
            DefaultRedstoneRoutingTechnology
        )
        Context.Resolve(BaseGate, DefaultRedstoneRoutingTechnology)
        with patch.object(
            GeometryRules,
            "LoadRoutingTemplates",
            return_value=ChangedTemplates,
        ):
            Resolved = Context.Resolve(
                TranslatedGate,
                DefaultRedstoneRoutingTechnology,
            )
            self.assertGeometryMatchesEager(
                Resolved,
                OriginalProducer(SimpleNamespace(
                    PlacedGates=[TranslatedGate]
                )),
            )
        self.assertEqual(Context.BuildReceipt().CacheHitCount, 0)

        Context = OrientedCellGeometryCacheContext(
            DefaultRedstoneRoutingTechnology
        )
        Context.Resolve(BaseGate, DefaultRedstoneRoutingTechnology)
        Macro = CellLibrary.CellMacros["NAND"]
        with patch.dict(
            CellLibrary.CellMacros,
            {"NAND": replace(Macro, EstimatedBlocks=Macro.EstimatedBlocks + 1)},
        ):
            Context.Resolve(
                TranslatedGate,
                DefaultRedstoneRoutingTechnology,
            )
        self.assertEqual(Context.BuildReceipt().CacheHitCount, 0)
        self.assertEqual(
            Context.BuildReceipt().EagerFallbackReasons,
            (("ContextIdentityChanged", 1),),
        )

        Context = OrientedCellGeometryCacheContext(
            DefaultRedstoneRoutingTechnology
        )
        Context.Resolve(BaseGate, DefaultRedstoneRoutingTechnology)
        with patch.object(
            PlacementCache,
            "ORIENTED_CELL_GEOMETRY_CACHE_SCHEMA",
            "portfolio-oriented-cell-geometry-v2",
        ):
            Context.Resolve(
                TranslatedGate,
                DefaultRedstoneRoutingTechnology,
            )
        self.assertEqual(Context.BuildReceipt().CacheHitCount, 0)
        self.assertEqual(
            Context.BuildReceipt().EagerFallbackReasons,
            (("ContextIdentityChanged", 1),),
        )

        Context = OrientedCellGeometryCacheContext(
            DefaultRedstoneRoutingTechnology
        )
        Context.Resolve(BaseGate, DefaultRedstoneRoutingTechnology)
        ChangedTechnology = replace(
            DefaultRedstoneRoutingTechnology,
            TrackPitch=DefaultRedstoneRoutingTechnology.TrackPitch + 1,
        )
        Resolved = Context.Resolve(TranslatedGate, ChangedTechnology)
        self.assertGeometryMatchesEager(
            Resolved,
            OriginalProducer(SimpleNamespace(PlacedGates=[TranslatedGate])),
            ChangedTechnology,
        )
        Receipt = Context.BuildReceipt()
        self.assertEqual(Receipt.CacheHitCount, 0)
        self.assertEqual(
            Receipt.EagerFallbackReasons,
            (("UnsupportedContextIdentity", 1),),
        )
        self.assertEqual(Receipt.ProducerCallCount, 2)

    def testProcessGlobalPortfolioRejectsChangedMacroBeforeLookup(
        self,
    ) -> None:
        Macro = CellLibrary.CellMacros["NAND"]
        Names, InternalByName = self.BuildFixture()
        Baseline = BuildPinAlignedPackedClusterPortfolio(
            Names,
            InternalByName,
            BeamWidth=8,
        )
        BaselineEntries = dict(
            PlacementCache._PinAlignedPackedClusterPortfolioCache
        )
        try:
            with patch.dict(
                CellLibrary.CellMacros,
                {"NAND": replace(Macro, Width=Macro.Width + 1)},
            ):
                Changed = BuildPinAlignedPackedClusterPortfolio(
                    Names,
                    InternalByName,
                    BeamWidth=8,
                )
                self.assertFalse(Changed.BuildReceipt.PortfolioCacheHit)
                PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()
                ChangedEager = BuildPinAlignedPackedClusterPortfolio(
                    Names,
                    InternalByName,
                    BeamWidth=8,
                    UseOrientedGeometryCache=False,
                )
                self.assertPortfoliosExactlyEqual(Changed, ChangedEager)
                self.assertNotEqual(Baseline.States, ChangedEager.States)
        finally:
            PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()
            PlacementCache._PinAlignedPackedClusterPortfolioCache.update(
                BaselineEntries
            )

        RestoredHit = BuildPinAlignedPackedClusterPortfolio(
            Names,
            InternalByName,
            BeamWidth=8,
        )
        self.assertPortfoliosExactlyEqual(RestoredHit, Baseline)
        self.assertTrue(RestoredHit.BuildReceipt.PortfolioCacheHit)
        self.assertEqual(RestoredHit.BuildReceipt.RequestCount, 0)
        self.assertEqual(RestoredHit.BuildReceipt.ProducerCallCount, 0)
        self.assertEqual(RestoredHit.BuildReceipt.MaximumEntryCount, 0)

    def testProcessGlobalPortfolioKeyCoversEveryProducerIdentityAxis(
        self,
    ) -> None:
        Templates = GeometryRules.LoadRoutingTemplates()
        ChangedTemplates = deepcopy(Templates)
        ChangedPosition = next(iter(ChangedTemplates["NAND"].Blocks))
        ChangedTemplates["NAND"].Blocks[ChangedPosition] = {
            "Name": "minecraft:air"
        }
        ChangedTechnology = replace(
            DefaultRedstoneRoutingTechnology,
            TrackPitch=DefaultRedstoneRoutingTechnology.TrackPitch + 1,
        )

        @contextmanager
        def ChangedTechnologyContext() -> object:
            with (
                patch.object(
                    CompactnessModule,
                    "DefaultRedstoneRoutingTechnology",
                    ChangedTechnology,
                ),
                patch.object(
                    ClusteringModule,
                    "DefaultRedstoneRoutingTechnology",
                    ChangedTechnology,
                ),
                patch.object(
                    GeometryRules,
                    "DefaultRedstoneRoutingTechnology",
                    ChangedTechnology,
                ),
            ):
                yield

        class AlternateRedstoneRoutingTechnology(
            RedstoneRoutingTechnology
        ):
            pass

        AlternateTechnology = AlternateRedstoneRoutingTechnology()

        @contextmanager
        def ChangedTechnologyClassContext() -> object:
            with (
                patch.object(
                    CompactnessModule,
                    "DefaultRedstoneRoutingTechnology",
                    AlternateTechnology,
                ),
                patch.object(
                    ClusteringModule,
                    "DefaultRedstoneRoutingTechnology",
                    AlternateTechnology,
                ),
                patch.object(
                    GeometryRules,
                    "DefaultRedstoneRoutingTechnology",
                    AlternateTechnology,
                ),
            ):
                yield

        Cases = (
            (
                "equal-value-new-template-generation",
                lambda: patch.object(
                    GeometryRules,
                    "LoadRoutingTemplates",
                    return_value=dict(Templates),
                ),
            ),
            (
                "changed-template-block-state",
                lambda: patch.object(
                    GeometryRules,
                    "LoadRoutingTemplates",
                    return_value=ChangedTemplates,
                ),
            ),
            (
                "changed-schema",
                lambda: patch.object(
                    PlacementCache,
                    "ORIENTED_CELL_GEOMETRY_CACHE_SCHEMA",
                    "portfolio-oriented-cell-geometry-v2",
                ),
            ),
            ("changed-technology-value", ChangedTechnologyContext),
            ("changed-technology-class", ChangedTechnologyClassContext),
        )
        for Name, MutationFactory in Cases:
            with self.subTest(Name=Name):
                PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()
                self.assertGlobalPortfolioIdentityMutationRebuilds(
                    MutationFactory
                )

    def testExtraLoadedTemplateContentInvalidatesGlobalPortfolio(
        self,
    ) -> None:
        Names, InternalByName = self.BuildReconvergentFixture()
        Templates = dict(GeometryRules.LoadRoutingTemplates())
        UnusedTemplate = deepcopy(Templates["NAND"])
        Templates["UNUSED"] = UnusedTemplate
        Position = next(iter(UnusedTemplate.Blocks))
        OriginalState = deepcopy(UnusedTemplate.Blocks[Position])

        with patch.object(
            GeometryRules,
            "LoadRoutingTemplates",
            return_value=Templates,
        ):
            Baseline = BuildPinAlignedPackedClusterPortfolio(
                Names,
                InternalByName,
                BeamWidth=8,
            )
            self.assertEqual(Baseline.RawCandidateCount, 8)
            self.assertEqual(Baseline.CandidateCount, 2)
            self.assertEqual(
                tuple(State.Fingerprint for State in Baseline.States),
                ("7963827a379d7887", "13fc8f905b0428ff"),
            )
            self.assertEqual(Baseline.BuildReceipt.RequestCount, 280)
            self.assertEqual(Baseline.BuildReceipt.CacheHitCount, 272)
            self.assertEqual(Baseline.BuildReceipt.CacheMissCount, 8)
            BaselineEntries = dict(
                PlacementCache._PinAlignedPackedClusterPortfolioCache
            )

            UnusedTemplate.Blocks[Position]["Name"] = "minecraft:air"
            Changed = BuildPinAlignedPackedClusterPortfolio(
                Names,
                InternalByName,
                BeamWidth=8,
            )
            self.assertFalse(Changed.BuildReceipt.PortfolioCacheHit)
            self.assertGreater(Changed.BuildReceipt.RequestCount, 0)
            PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()
            CurrentEager = BuildPinAlignedPackedClusterPortfolio(
                Names,
                InternalByName,
                BeamWidth=8,
                UseOrientedGeometryCache=False,
            )
            self.assertPortfoliosExactlyEqual(Changed, CurrentEager)

            UnusedTemplate.Blocks[Position] = OriginalState
            PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()
            PlacementCache._PinAlignedPackedClusterPortfolioCache.update(
                BaselineEntries
            )
            RestoredHit = BuildPinAlignedPackedClusterPortfolio(
                Names,
                InternalByName,
                BeamWidth=8,
            )
            self.assertPortfoliosExactlyEqual(RestoredHit, Baseline)
            self.assertTrue(RestoredHit.BuildReceipt.PortfolioCacheHit)
            self.assertEqual(RestoredHit.BuildReceipt.RequestCount, 0)
            self.assertEqual(RestoredHit.BuildReceipt.ProducerCallCount, 0)
            self.assertEqual(RestoredHit.BuildReceipt.MaximumEntryCount, 0)

            for State in Baseline.States:
                Positions, Rotations, Mirrors = State.Materialize()
                Placed = SimpleNamespace(PlacedGates=[
                    BuildPlacedGate(
                        InternalByName[Name],
                        Positions[Name][0],
                        1,
                        Positions[Name][1],
                        Rotations[Name],
                        Mirrors[Name],
                    )
                    for Name in Names
                ])
                GeometryRules.BuildPlacedCellGeometryWithKeepOut(Placed)
                GeometryRules.ValidatePlacedCellElectricalIsolation(Placed)

    def testEagerPortfolioCannotPublishLegacyHelperGeometry(
        self,
    ) -> None:
        Names, InternalByName = self.BuildReconvergentFixture()
        Templates = deepcopy(GeometryRules.LoadRoutingTemplates())
        OriginalNandBlocks = deepcopy(Templates["NAND"].Blocks)

        with patch.object(
            GeometryRules,
            "LoadRoutingTemplates",
            return_value=Templates,
        ):
            self.ClearLegacyConflictHelpers()
            Baseline = BuildPinAlignedPackedClusterPortfolio(
                Names,
                InternalByName,
                BeamWidth=8,
                UseOrientedGeometryCache=False,
            )
            self.assertEqual(Baseline.RawCandidateCount, 8)
            self.assertEqual(Baseline.CandidateCount, 2)
            self.assertEqual(
                tuple(State.Fingerprint for State in Baseline.States),
                ("7963827a379d7887", "13fc8f905b0428ff"),
            )
            BaselineEntries = dict(
                PlacementCache._PinAlignedPackedClusterPortfolioCache
            )

            for Position in tuple(Templates["NAND"].Blocks):
                Templates["NAND"].Blocks[Position] = {
                    "Name": "minecraft:air"
                }
            ChangedEager = BuildPinAlignedPackedClusterPortfolio(
                Names,
                InternalByName,
                BeamWidth=8,
                UseOrientedGeometryCache=False,
            )
            self.assertFalse(ChangedEager.BuildReceipt.PortfolioCacheHit)
            self.assertEqual(ChangedEager.RawCandidateCount, 8)
            self.assertEqual(ChangedEager.CandidateCount, 1)
            self.assertEqual(
                tuple(State.Fingerprint for State in ChangedEager.States),
                ("1750945486c275b9",),
            )
            self.assertEqual(
                tuple(State.Objective for State in ChangedEager.States),
                ((0, 0, 36, 9, 8),),
            )
            ChangedCachedHit = BuildPinAlignedPackedClusterPortfolio(
                Names,
                InternalByName,
                BeamWidth=8,
            )
            self.assertPortfoliosExactlyEqual(
                ChangedCachedHit,
                ChangedEager,
            )
            self.assertTrue(
                ChangedCachedHit.BuildReceipt.PortfolioCacheHit
            )
            self.assertEqual(ChangedCachedHit.BuildReceipt.RequestCount, 0)

            PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()
            self.ClearLegacyConflictHelpers()
            FreshChangedEager = BuildPinAlignedPackedClusterPortfolio(
                Names,
                InternalByName,
                BeamWidth=8,
                UseOrientedGeometryCache=False,
            )
            self.assertPortfoliosExactlyEqual(
                ChangedEager,
                FreshChangedEager,
            )
            for State in FreshChangedEager.States:
                Positions, Rotations, Mirrors = State.Materialize()
                Placed = SimpleNamespace(PlacedGates=[
                    BuildPlacedGate(
                        InternalByName[Name],
                        Positions[Name][0],
                        1,
                        Positions[Name][1],
                        Rotations[Name],
                        Mirrors[Name],
                    )
                    for Name in Names
                ])
                GeometryRules.BuildPlacedCellGeometryWithKeepOut(Placed)
                GeometryRules.ValidatePlacedCellElectricalIsolation(Placed)

            Templates["NAND"].Blocks = OriginalNandBlocks
            PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()
            PlacementCache._PinAlignedPackedClusterPortfolioCache.update(
                BaselineEntries
            )
            RestoredHit = BuildPinAlignedPackedClusterPortfolio(
                Names,
                InternalByName,
                BeamWidth=8,
                UseOrientedGeometryCache=False,
            )
            self.assertPortfoliosExactlyEqual(RestoredHit, Baseline)
            self.assertTrue(RestoredHit.BuildReceipt.PortfolioCacheHit)
            self.assertEqual(RestoredHit.BuildReceipt.RequestCount, 0)
            self.assertEqual(RestoredHit.BuildReceipt.ProducerCallCount, 0)

    def testPortfolioResolverUsesCurrentAccessLengthWithoutHelperClear(
        self,
    ) -> None:
        First = BuildPlacedGate(
            self.BuildCellGate(GateKind.INPUT),
            0,
            1,
            0,
            0,
        )
        Second = BuildPlacedGate(
            self.BuildCellGate(GateKind.NAND),
            -4,
            1,
            0,
            0,
        )
        self.ClearLegacyConflictHelpers()
        self.assertFalse(PcbGatesConflict(First, Second))
        ChangedTechnology = replace(
            DefaultRedstoneRoutingTechnology,
            AccessLength=DefaultRedstoneRoutingTechnology.AccessLength + 1,
        )

        with (
            patch.object(
                CompactnessModule,
                "DefaultRedstoneRoutingTechnology",
                ChangedTechnology,
            ),
            patch.object(
                ClusteringModule,
                "DefaultRedstoneRoutingTechnology",
                ChangedTechnology,
            ),
            patch.object(
                GeometryRules,
                "DefaultRedstoneRoutingTechnology",
                ChangedTechnology,
            ),
        ):
            CurrentIdentity = BuildOrientedGeometryPortfolioCacheIdentity(
                ChangedTechnology
            )
            Resolver = EagerCurrentCellGeometryResolver(
                ChangedTechnology,
                ExpectedPortfolioIdentity=CurrentIdentity,
            )
            with self.assertRaisesRegex(
                ValueError,
                "pin-access path exceeds its catalog pattern",
            ):
                PcbGatesConflict(First, Second, Resolver)

            Names, InternalByName = self.BuildReconvergentFixture()
            with self.assertRaisesRegex(
                ValueError,
                "pin-access path exceeds its catalog pattern",
            ):
                BuildPinAlignedPackedClusterPortfolio(
                    Names,
                    InternalByName,
                    BeamWidth=8,
                    UseOrientedGeometryCache=False,
                )
            self.assertEqual(
                PlacementCache._PinAlignedPackedClusterPortfolioCache,
                {},
            )

            self.ClearLegacyConflictHelpers()
            FreshResolver = EagerCurrentCellGeometryResolver(
                ChangedTechnology,
                ExpectedPortfolioIdentity=CurrentIdentity,
            )
            with self.assertRaisesRegex(
                ValueError,
                "pin-access path exceeds its catalog pattern",
            ):
                PcbGatesConflict(First, Second, FreshResolver)

    def testReceiptRejectsEveryMalformedMetricDomain(self) -> None:
        CountFields = (
            "RequestCount",
            "CacheHitCount",
            "CacheMissCount",
            "EagerFallbackCount",
            "ProducerCallCount",
            "AvoidedGeometryExpansionCount",
            "TranslationMaterializationCount",
            "RetainedEntryCount",
            "MaximumEntryCount",
        )
        for FieldName in CountFields:
            for InvalidValue in (-1, 0.5, False):
                with self.subTest(
                    FieldName=FieldName,
                    InvalidValue=InvalidValue,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "nonnegative integers",
                    ):
                        OrientedGeometryCacheBuildReceipt(**{
                            FieldName: InvalidValue
                        })

        ValidFallback = {
            "RequestCount": 2,
            "EagerFallbackCount": 2,
            "ProducerCallCount": 2,
        }
        InvalidReasons = (
            [("Reason", 2)],
            (("", 2),),
            ((" Reason", 2),),
            (("Reason ", 2),),
            (("Reason", 0),),
            (("Reason", -1),),
            (("Reason", True),),
            (("Reason", 2.0),),
            (("Second", 1), ("First", 1)),
            (("Reason", 1), ("Reason", 1)),
        )
        for Reasons in InvalidReasons:
            with self.subTest(Reasons=Reasons):
                with self.assertRaises(ValueError):
                    OrientedGeometryCacheBuildReceipt(
                        **ValidFallback,
                        EagerFallbackReasons=Reasons,
                    )
        with self.assertRaisesRegex(ValueError, "fallback reasons"):
            OrientedGeometryCacheBuildReceipt(
                **ValidFallback,
                EagerFallbackReasons=(("Reason", 1),),
            )
        with self.assertRaisesRegex(ValueError, "retained-entry bound"):
            OrientedGeometryCacheBuildReceipt(
                RetainedEntryCount=2,
                MaximumEntryCount=1,
            )
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            OrientedGeometryCacheBuildReceipt(PortfolioCacheHit=1)
        self.assertEqual(
            OrientedGeometryCacheBuildReceipt(
                **ValidFallback,
                EagerFallbackReasons=(("Reason", 2),),
            ).EagerFallbackReasons,
            (("Reason", 2),),
        )

    def testRecursiveTemplateStatePreservesEagerRejectionWithoutPublication(
        self,
    ) -> None:
        OriginalProducer = (
            GeometryRules.BuildPlacedCellGeometryWithKeepOut
        )
        RecursiveTemplates = deepcopy(
            GeometryRules.LoadRoutingTemplates()
        )
        Position = next(iter(RecursiveTemplates["NAND"].Blocks))
        RecursiveState = RecursiveTemplates["NAND"].Blocks[Position]
        RecursiveState["Recursive"] = RecursiveState
        GateValue = BuildPlacedGate(
            self.BuildCellGate(GateKind.NAND),
            9,
            3,
            -7,
            90,
            True,
        )

        def CaptureTemplateStateError(
            CallableValue: object,
        ) -> tuple[type[ValueError], str]:
            with self.assertRaisesRegex(
                ValueError,
                "placed template state has fields the transform cannot preserve",
            ) as Raised:
                CallableValue()
            return type(Raised.exception), str(Raised.exception)

        with patch.object(
            GeometryRules,
            "LoadRoutingTemplates",
            return_value=RecursiveTemplates,
        ):
            EagerError = CaptureTemplateStateError(
                lambda: OriginalProducer(
                    SimpleNamespace(PlacedGates=[GateValue])
                )
            )
            Context = OrientedCellGeometryCacheContext(
                DefaultRedstoneRoutingTechnology
            )
            CachedResolverError = CaptureTemplateStateError(
                lambda: Context.Resolve(
                    GateValue,
                    DefaultRedstoneRoutingTechnology,
                )
            )
            self.assertEqual(CachedResolverError, EagerError)
            Receipt = Context.BuildReceipt()
            self.assertEqual(Receipt.CacheHitCount, 0)
            self.assertEqual(Receipt.CacheMissCount, 0)
            self.assertGreater(Receipt.EagerFallbackCount, 0)
            self.assertEqual(Receipt.RetainedEntryCount, 0)
            self.assertEqual(Receipt.MaximumEntryCount, 0)
            self.assertEqual(
                Receipt.EagerFallbackReasons,
                (("UnsupportedContextIdentity", 1),),
            )

            Names, InternalByName = self.BuildFixture()
            PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()
            CachedPortfolioError = CaptureTemplateStateError(
                lambda: BuildPinAlignedPackedClusterPortfolio(
                    Names,
                    InternalByName,
                    BeamWidth=8,
                )
            )
            self.assertEqual(CachedPortfolioError, EagerError)
            self.assertEqual(
                PlacementCache._PinAlignedPackedClusterPortfolioCache,
                {},
            )
            EagerPortfolioError = CaptureTemplateStateError(
                lambda: BuildPinAlignedPackedClusterPortfolio(
                    Names,
                    InternalByName,
                    BeamWidth=8,
                    UseOrientedGeometryCache=False,
                )
            )
            self.assertEqual(EagerPortfolioError, EagerError)
            self.assertEqual(
                PlacementCache._PinAlignedPackedClusterPortfolioCache,
                {},
            )

        PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()
        CachedPortfolio = BuildPinAlignedPackedClusterPortfolio(
            Names,
            InternalByName,
            BeamWidth=8,
        )
        self.assertFalse(CachedPortfolio.BuildReceipt.PortfolioCacheHit)
        self.assertGreater(CachedPortfolio.BuildReceipt.RequestCount, 0)
        self.assertEqual(CachedPortfolio.BuildReceipt.EagerFallbackCount, 0)
        self.assertTrue(CachedPortfolio.States)
        self.assertGreater(CachedPortfolio.RawCandidateCount, 0)

        PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()
        EagerPortfolio = BuildPinAlignedPackedClusterPortfolio(
            Names,
            InternalByName,
            BeamWidth=8,
            UseOrientedGeometryCache=False,
        )
        self.assertFalse(EagerPortfolio.BuildReceipt.PortfolioCacheHit)
        self.assertPortfoliosExactlyEqual(CachedPortfolio, EagerPortfolio)

    def testUnknownKindAndMalformedTransformPreserveEagerErrors(
        self,
    ) -> None:
        OriginalProducer = (
            GeometryRules.BuildPlacedCellGeometryWithKeepOut
        )
        Unknown = SimpleNamespace(
            Name="Unknown",
            Kind="UNKNOWN",
            X=0,
            Y=1,
            Z=0,
            Rotation=0,
            MirrorX=False,
        )
        Malformed = SimpleNamespace(
            Name="Malformed",
            Kind="NAND",
            X=0,
            Y=1,
            Z=0,
            Rotation=45,
            MirrorX=False,
        )

        def CaptureError(CallableValue: object) -> tuple[type, str]:
            try:
                CallableValue()
            except Exception as Error:
                return type(Error), str(Error)
            self.fail("expected eager geometry to reject the gate")

        Context = OrientedCellGeometryCacheContext(
            DefaultRedstoneRoutingTechnology
        )
        for GateValue in (Unknown, Malformed):
            EagerError = CaptureError(lambda: OriginalProducer(
                SimpleNamespace(PlacedGates=[GateValue])
            ))
            CachedError = CaptureError(lambda: Context.Resolve(
                GateValue,
                DefaultRedstoneRoutingTechnology,
            ))
            self.assertEqual(CachedError, EagerError)
        Receipt = Context.BuildReceipt()
        self.assertEqual(Receipt.CacheHitCount, 0)
        self.assertEqual(Receipt.EagerFallbackCount, 2)
        self.assertEqual(Receipt.ProducerCallCount, 2)
        self.assertEqual(
            Receipt.EagerFallbackReasons,
            (("MalformedTransform", 1), ("UnknownCellKind", 1)),
        )

    def testDisallowedMirrorCanonicalizesToTheNonMirroredEntry(self) -> None:
        Input = BuildPlacedGate(
            self.BuildCellGate(GateKind.INPUT),
            3,
            2,
            5,
            270,
            False,
        )
        NonCanonicalInput = replace(
            BuildPlacedGate(
                self.BuildCellGate(GateKind.INPUT),
                -7,
                4,
                11,
                270,
                False,
            ),
            MirrorX=True,
        )
        Expected = BuildPlacedGate(
            self.BuildCellGate(GateKind.INPUT),
            -7,
            4,
            11,
            270,
            False,
        )
        Context = OrientedCellGeometryCacheContext(
            DefaultRedstoneRoutingTechnology
        )

        Context.Resolve(Input, DefaultRedstoneRoutingTechnology)
        Resolved = Context.Resolve(
            NonCanonicalInput,
            DefaultRedstoneRoutingTechnology,
        )
        self.assertGeometryMatchesEager(
            Resolved,
            GeometryRules.BuildPlacedCellGeometryWithKeepOut(
                SimpleNamespace(PlacedGates=[Expected])
            ),
        )
        Receipt = Context.BuildReceipt()
        self.assertEqual(Receipt.CacheMissCount, 1)
        self.assertEqual(Receipt.CacheHitCount, 1)
        self.assertEqual(Receipt.ProducerCallCount, 1)

    def testConflictContextPreservesConflictAndNonConflictDecisions(
        self,
    ) -> None:
        InputConflict = BuildPlacedGate(
            self.BuildCellGate(GateKind.INPUT),
            8,
            1,
            -1,
            0,
        )
        NandConflict = BuildPlacedGate(
            self.BuildCellGate(GateKind.NAND),
            5,
            1,
            0,
            0,
        )
        InputClear = BuildPlacedGate(
            self.BuildCellGate(GateKind.INPUT),
            0,
            1,
            0,
            0,
        )
        NandClear = BuildPlacedGate(
            self.BuildCellGate(GateKind.NAND),
            -4,
            1,
            0,
            0,
        )
        Context = OrientedCellGeometryCacheContext(
            DefaultRedstoneRoutingTechnology
        )

        self.assertEqual(
            PcbGatesConflict(InputConflict, NandConflict, Context),
            PcbGatesConflict(InputConflict, NandConflict),
        )
        self.assertEqual(
            PcbGatesConflict(InputClear, NandClear, Context),
            PcbGatesConflict(InputClear, NandClear),
        )
        Receipt = Context.BuildReceipt()
        self.assertEqual(Receipt.RequestCount, 4)
        self.assertEqual(Receipt.CacheMissCount, 2)
        self.assertEqual(Receipt.CacheHitCount, 2)

    def testCachedPortfolioMatchesEagerAndReceiptsAreInvocationLocal(
        self,
    ) -> None:
        Names, InternalByName = self.BuildFixture()
        OriginalProducer = (
            GeometryRules.BuildPlacedCellGeometryWithKeepOut
        )
        Eager = BuildPinAlignedPackedClusterPortfolio(
            Names,
            InternalByName,
            BeamWidth=8,
            UseOrientedGeometryCache=False,
        )
        CrossModeHit = BuildPinAlignedPackedClusterPortfolio(
            Names,
            InternalByName,
            BeamWidth=8,
        )
        self.assertPortfoliosExactlyEqual(CrossModeHit, Eager)
        self.assertTrue(CrossModeHit.BuildReceipt.PortfolioCacheHit)
        self.assertEqual(CrossModeHit.BuildReceipt.RequestCount, 0)
        self.assertEqual(CrossModeHit.BuildReceipt.ProducerCallCount, 0)
        PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()
        ProducerCalls = 0

        def ObserveProducer(Placed: object) -> tuple[set, set, set, set]:
            nonlocal ProducerCalls
            ProducerCalls += 1
            return OriginalProducer(Placed)

        with patch.object(
            GeometryRules,
            "BuildPlacedCellGeometryWithKeepOut",
            side_effect=ObserveProducer,
        ):
            Cached = BuildPinAlignedPackedClusterPortfolio(
                Names,
                InternalByName,
                BeamWidth=8,
            )
            CallsAfterFirst = ProducerCalls
            GlobalHit = BuildPinAlignedPackedClusterPortfolio(
                Names,
                InternalByName,
                BeamWidth=8,
            )

        self.assertEqual(Cached, Eager)
        self.assertEqual(Cached.States, Eager.States)
        self.assertEqual(Cached.RawCandidateCount, Eager.RawCandidateCount)
        self.assertEqual(
            tuple(State.Materialize() for State in Cached.States),
            tuple(State.Materialize() for State in Eager.States),
        )
        Receipt = Cached.BuildReceipt
        self.assertGreater(Receipt.RequestCount, 0)
        self.assertGreater(Receipt.CacheHitCount, 0)
        self.assertGreater(Receipt.CacheMissCount, 0)
        self.assertEqual(Receipt.EagerFallbackCount, 0)
        self.assertEqual(Receipt.ProducerCallCount, CallsAfterFirst)
        self.assertEqual(ProducerCalls, CallsAfterFirst)
        self.assertLessEqual(
            Receipt.RetainedEntryCount,
            Receipt.MaximumEntryCount,
        )
        self.assertEqual(GlobalHit, Cached)
        self.assertTrue(GlobalHit.BuildReceipt.PortfolioCacheHit)
        self.assertEqual(GlobalHit.BuildReceipt.RequestCount, 0)
        self.assertEqual(GlobalHit.BuildReceipt.ProducerCallCount, 0)
        self.assertEqual(GlobalHit.BuildReceipt.RetainedEntryCount, 0)
        self.assertEqual(GlobalHit.BuildReceipt.MaximumEntryCount, 0)

        for State in Cached.States:
            Positions, Rotations, Mirrors = State.Materialize()
            PlacedGates = [
                BuildPlacedGate(
                    InternalByName[Name],
                    Positions[Name][0],
                    1,
                    Positions[Name][1],
                    Rotations[Name],
                    Mirrors[Name],
                )
                for Name in Names
            ]
            Placement = SimpleNamespace(PlacedGates=PlacedGates)
            OriginalProducer(Placement)
            GeometryRules.ValidatePlacedCellElectricalIsolation(Placement)

        PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()
        FirstUncached = BuildPinAlignedPackedClusterPortfolio(
            Names,
            InternalByName,
            BeamWidth=8,
        )
        PlacementCache._PinAlignedPackedClusterPortfolioCache.clear()
        SecondUncached = BuildPinAlignedPackedClusterPortfolio(
            Names,
            InternalByName,
            BeamWidth=8,
        )
        self.assertFalse(FirstUncached.BuildReceipt.PortfolioCacheHit)
        self.assertFalse(SecondUncached.BuildReceipt.PortfolioCacheHit)
        self.assertEqual(
            FirstUncached.BuildReceipt.ProducerCallCount,
            SecondUncached.BuildReceipt.ProducerCallCount,
        )
        self.assertGreater(SecondUncached.BuildReceipt.ProducerCallCount, 0)

    def testPortfolioStatesAreStableNondominatedAndMaterializable(self) -> None:
        Names, InternalByName = self.BuildFixture()

        First = BuildPinAlignedPackedClusterPortfolio(
            Names,
            InternalByName,
            BeamWidth=8,
        )
        Second = BuildPinAlignedPackedClusterPortfolio(
            Names,
            InternalByName,
            BeamWidth=8,
        )

        self.assertGreater(First.CandidateCount, 0)
        self.assertEqual(First, Second)
        self.assertGreaterEqual(First.RawCandidateCount, First.CandidateCount)
        self.assertEqual(
            CountPinAlignedPackedClusterPortfolio(
                Names,
                InternalByName,
                BeamWidth=8,
            ),
            First.CandidateCount,
        )
        self.assertEqual(
            len({State.CandidateIndex for State in First.States}),
            First.CandidateCount,
        )
        for State in First.States:
            self.assertFalse(any(
                all(
                    OtherValue <= StateValue
                    for OtherValue, StateValue in zip(
                        Other.Objective,
                        State.Objective,
                    )
                )
                and any(
                    OtherValue < StateValue
                    for OtherValue, StateValue in zip(
                        Other.Objective,
                        State.Objective,
                    )
                )
                for Other in First.States
                if Other is not State
            ))
            Candidate = BuildPinAlignedPackedCluster(
                Names,
                InternalByName,
                BeamWidth=8,
                CandidateIndex=State.CandidateIndex,
            )
            self.assertEqual(Candidate, State.Materialize())
            assert Candidate is not None
            Candidate[0]["N0"] = (999, 999)
            self.assertEqual(
                BuildPinAlignedPackedCluster(
                    Names,
                    InternalByName,
                    BeamWidth=8,
                    CandidateIndex=State.CandidateIndex,
                ),
                State.Materialize(),
            )

    def testIndexedBuilderRejectsOutOfRangeStateInsteadOfFallingBack(self) -> None:
        Names, InternalByName = self.BuildFixture()
        Portfolio = BuildPinAlignedPackedClusterPortfolio(
            Names,
            InternalByName,
            BeamWidth=8,
        )

        with self.assertRaisesRegex(
            ValueError,
            "candidate index exceeds retained state count",
        ):
            BuildPinAlignedPackedCluster(
                Names,
                InternalByName,
                BeamWidth=8,
                CandidateIndex=Portfolio.RawCandidateCount,
            )

    def testPortfolioRepresentsNoLegalGraphContinuationAsEmptyDomain(self) -> None:
        Gates = (
            Gate("N0", GateKind.NAND, ["S0"], ["A", "B"]),
            Gate("N1", GateKind.NAND, ["S1"], ["C", "D"]),
        )
        InternalByName = {GateValue.Name: GateValue for GateValue in Gates}
        Names = tuple(InternalByName)

        Portfolio = BuildPinAlignedPackedClusterPortfolio(
            Names,
            InternalByName,
            BeamWidth=8,
        )

        self.assertEqual(Portfolio.States, ())
        self.assertEqual(Portfolio.RawCandidateCount, 0)
        self.assertIsNone(BuildPinAlignedPackedCluster(
            Names,
            InternalByName,
            BeamWidth=8,
        ))


if __name__ == "__main__":
    unittest.main()
