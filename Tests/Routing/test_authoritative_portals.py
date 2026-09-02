"""Portals contracts for authoritative routing."""

from ._authoritative_planner_contracts import *


class AuthoritativePortalsTests(AuthoritativePlannerTestBase):
    def testExactPortalConstraintFactorsCaptureCrossVariableAirTernary(
        self,
    ) -> None:
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Domains = (
            ExactPortalConstraintVariableDomain(
                Variable="A",
                Signal="Signal",
                Choices=(ExactPortalConstraintChoice(
                    ChoiceId="a",
                    Nodes=frozenset({(0, 1, 0)}),
                ),),
            ),
            ExactPortalConstraintVariableDomain(
                Variable="B",
                Signal="Signal",
                Choices=(ExactPortalConstraintChoice(
                    ChoiceId="b",
                    Nodes=frozenset({(1, 2, 0)}),
                ),),
            ),
            ExactPortalConstraintVariableDomain(
                Variable="C",
                Signal="Signal",
                Choices=(ExactPortalConstraintChoice(
                    ChoiceId="c",
                    Nodes=frozenset({(0, 3, 0)}),
                ),),
            ),
        )

        Extraction = ExtractExactPortalConstraintFactors(Domains, Graph)
        Sparse = ExtractSparseExactPortalConstraintFactors(Domains, Graph)

        self.assertTrue(Extraction.Complete)
        self.assertTrue(Sparse.Complete)
        self.assertEqual(
            Sparse.ForbiddenTuples,
            Extraction.ForbiddenTuples,
        )
        self.assertEqual(Extraction.MaximumForbiddenTupleArity, 3)
        self.assertEqual(
            tuple(Value.Assignments for Value in Extraction.ForbiddenTuples),
            ((('A', 'a'), ('B', 'b'), ('C', 'c')),),
        )
        self.assertEqual(
            Extraction.ForbiddenTuples[0].ConflictPositions,
            frozenset({(0, 2, 0)}),
        )
        # No unary or binary projection sees the air reservation created by
        # A+B colliding with C's support at (0, 2, 0).
        for Assignment in (
            {"A": "a", "B": "b"},
            {"A": "a", "C": "c"},
            {"B": "b", "C": "c"},
        ):
            self.assertTrue(ExactPortalConstraintAssignmentSatisfiesFactors(
                Assignment,
                Extraction.ForbiddenTuples,
            ))
        self.assertFalse(ExactPortalConstraintAssignmentSatisfiesFactors(
            {"A": "a", "B": "b", "C": "c"},
            Extraction.ForbiddenTuples,
        ))

    def testExactPortalConstraintFactorsMatchBruteForceAssignments(
        self,
    ) -> None:
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Domains = tuple(
            ExactPortalConstraintVariableDomain(
                Variable=Variable,
                Signal="Signal",
                Choices=(
                    ExactPortalConstraintChoice(
                        ChoiceId=Variable.lower() + "0",
                        Nodes=frozenset({First}),
                    ),
                    ExactPortalConstraintChoice(
                        ChoiceId=Variable.lower() + "1",
                        Nodes=frozenset({Second}),
                    ),
                ),
            )
            for Variable, First, Second in (
                ("A", (0, 1, 0), (10, 1, 0)),
                ("B", (1, 2, 0), (12, 1, 0)),
                ("C", (0, 3, 0), (14, 1, 0)),
            )
        )
        Extraction = ExtractExactPortalConstraintFactors(Domains, Graph)

        self.assertTrue(Extraction.Complete)
        for First in Domains[0].Choices:
            for Second in Domains[1].Choices:
                for Third in Domains[2].Choices:
                    Choices = (First, Second, Third)
                    Assignment = {
                        Domain.Variable: Choice.ChoiceId
                        for Domain, Choice in zip(Domains, Choices)
                    }
                    Claims = Graph.BuildRouteClaims(frozenset(
                        Position
                        for Choice in Choices
                        for Position in Choice.Nodes
                    ))
                    BruteForceLegal = not any((
                        Claims.RequiredAirCells & Claims.WireCells,
                        Claims.SupportCells & (
                            Claims.WireCells | Claims.RequiredAirCells
                        ),
                    ))
                    self.assertEqual(
                        ExactPortalConstraintAssignmentSatisfiesFactors(
                            Assignment,
                            Extraction.ForbiddenTuples,
                        ),
                        BruteForceLegal,
                    )

        Projection = ProjectExactPortalConstraintFactors(
            Extraction,
            Domains,
            ("A", "C"),
        )
        ExpectedPairs = tuple(
            (First.ChoiceId, Third.ChoiceId)
            for First in Domains[0].Choices
            for Third in Domains[2].Choices
            if any(
                ExactPortalConstraintAssignmentSatisfiesFactors(
                    {
                        "A": First.ChoiceId,
                        "B": Second.ChoiceId,
                        "C": Third.ChoiceId,
                    },
                    Extraction.ForbiddenTuples,
                )
                for Second in Domains[1].Choices
            )
        )
        self.assertTrue(Projection.Complete)
        self.assertEqual(Projection.SupportedChoicePairs, ExpectedPairs)
        self.assertFalse(ProjectExactPortalConstraintFactors(
            Extraction,
            Domains,
            ("A", "C"),
            ShouldStop=lambda: True,
        ).Complete)

    def testSparsePortalConstraintFactorsMatchReferenceFixtures(self) -> None:
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Generator = Random(903_217)
        Positions = tuple(
            (X, Y, Z)
            for X in range(4)
            for Y in range(1, 4)
            for Z in range(2)
        )
        for FixtureIndex in range(20):
            Domains = tuple(
                ExactPortalConstraintVariableDomain(
                    Variable=f"V{VariableIndex}",
                    Signal=(
                        "First"
                        if VariableIndex < 2
                        else "Second"
                    ),
                    Choices=tuple(
                        ExactPortalConstraintChoice(
                            ChoiceId=(
                                f"v{VariableIndex}:c{ChoiceIndex}"
                            ),
                            Nodes=frozenset(Generator.sample(
                                Positions,
                                1 + Generator.randrange(2),
                            )),
                        )
                        for ChoiceIndex in range(2)
                    ),
                )
                for VariableIndex in range(3)
            )
            Reference = ExtractExactPortalConstraintFactors(Domains, Graph)
            Sparse = ExtractSparseExactPortalConstraintFactors(Domains, Graph)
            self.assertTrue(Reference.Complete, FixtureIndex)
            self.assertTrue(Sparse.Complete, FixtureIndex)
            self.assertEqual(
                Sparse.ForbiddenTuples,
                Reference.ForbiddenTuples,
                FixtureIndex,
            )

    def testPostClosurePortalCompletionPreservesPolicyEmptyProofs(
        self,
    ) -> None:
        PolicyKey = ("Alpha", (1, 2, 3), 3)
        RequestKey = ("Alpha", (1, 2, 3), 1)

        self.assertEqual(
            MergePostClosurePortalCompletionKeys(
                (PolicyKey,),
                (RequestKey, RequestKey),
            ),
            tuple(sorted((PolicyKey, RequestKey))),
        )

    def testFrozenComponentPortalTupleFilterIgnoresSameOwnerOnly(
        self,
    ) -> None:
        CandidateClaims = RoutingResourceClaims(
            WireCells=frozenset(((8, 2, 8),)),
            ElectricalCells=frozenset(((8, 2, 8),)),
        )
        SameOwner = LocalRouteClaim(
            ClusterId=-1,
            Signal="Owned",
            Root=(8, 2, 8),
            ConnectedTargets=((8, 2, 8),),
            Nodes=frozenset(((8, 2, 8),)),
            Edges=frozenset(),
            BoundaryNodes=((8, 2, 8),),
            Claims=CandidateClaims,
        )
        ForeignOwner = LocalRouteClaim(
            ClusterId=-1,
            Signal="Foreign",
            Root=(9, 2, 8),
            ConnectedTargets=((9, 2, 8),),
            Nodes=frozenset(((9, 2, 8),)),
            Edges=frozenset(),
            BoundaryNodes=((9, 2, 8),),
            Claims=RoutingResourceClaims(
                WireCells=frozenset(((9, 2, 8),)),
                ElectricalCells=frozenset(((8, 2, 8),)),
            ),
        )

        self.assertEqual(
            PortalTupleConflictsWithFrozenComponentClaims(
                "Owned",
                CandidateClaims,
                (SameOwner, ForeignOwner),
            ),
            ("Foreign",),
        )
        self.assertEqual(
            PortalTupleConflictsWithFrozenComponentClaims(
                "Owned",
                CandidateClaims,
                (SameOwner,),
            ),
            (),
        )

    def testCoordinatedPortalVariantsStayWithinExistingDemandCap(
        self,
    ) -> None:
        self.assertEqual(
            SelectCoordinatedPortalVariantCount(2, 13, True),
            6,
        )
        self.assertEqual(
            SelectCoordinatedPortalVariantCount(2, 4, True),
            4,
        )
        self.assertEqual(
            SelectCoordinatedPortalVariantCount(2, 13, False),
            2,
        )
        self.assertEqual(
            SelectCoordinatedPortalVariantCount(8, 13, True),
            8,
        )

    def testMaturePortfolioPortalLimitCapPreservesSmallerDomains(
        self,
    ) -> None:
        self.assertEqual(
            SelectMaturePortfolioPortalLimit(13, True),
            6,
        )
        self.assertEqual(
            SelectMaturePortfolioPortalLimit(6, True),
            6,
        )
        self.assertEqual(
            SelectMaturePortfolioPortalLimit(4, True),
            4,
        )
        self.assertEqual(
            SelectMaturePortfolioPortalLimit(13, False),
            13,
        )
        with self.assertRaises(ValueError):
            SelectMaturePortfolioPortalLimit(0, True)
        with self.assertRaises(ValueError):
            SelectMaturePortfolioPortalLimit(
                13,
                True,
                MaximumMaturePortfolioPortalLimit=0,
            )

    def testCompleteEmptyPortalTupleDomainIsExactRenameIndependentCut(
        self,
    ) -> None:
        First = BuildMandatoryPortalTupleSelfConflictFailure(
            (
                MandatoryPortalTupleSelfConflictEvidence(
                    Signal="GeneratedNet26",
                    CompletePortalTupleCount=1,
                    EvaluatedPortalTupleCount=1,
                    TerminalPortalDomainCounts=(1, 1, 1),
                    ConflictResources=(
                        RoutingResourceId(
                            RoutingResourceKind.Support,
                            (20, 4, 31),
                        ),
                        RoutingResourceId(
                            RoutingResourceKind.Air,
                            (21, 5, 31),
                        ),
                    ),
                ),
            ),
            StageTimings={"MandatoryPortalClaimPreScreen": 0.01},
        )
        Renamed = BuildMandatoryPortalTupleSelfConflictFailure(
            (
                MandatoryPortalTupleSelfConflictEvidence(
                    Signal="RenamedEndpoint",
                    CompletePortalTupleCount=1,
                    EvaluatedPortalTupleCount=1,
                    TerminalPortalDomainCounts=(1, 1, 1),
                    ConflictResources=(
                        RoutingResourceId(
                            RoutingResourceKind.Support,
                            (120, 14, 231),
                        ),
                        RoutingResourceId(
                            RoutingResourceKind.Air,
                            (121, 15, 231),
                        ),
                    ),
                ),
            ),
        )

        self.assertEqual(
            First.Reason,
            RoutingFailureReason.NoPinAccessPattern,
        )
        self.assertEqual(First.Stage, "InitialCandidateAssignment")
        self.assertEqual(
            First.Diagnostics["ConflictGraph"]["Classification"],
            "mandatory-access-self-conflict",
        )
        self.assertEqual(
            First.Diagnostics["MandatoryAccessProof"][
                "ConflictFingerprint"
            ],
            Renamed.Diagnostics["MandatoryAccessProof"][
                "ConflictFingerprint"
            ],
        )
        self.assertEqual(
            First.Diagnostics["ConflictFingerprint"],
            Renamed.Diagnostics["ConflictFingerprint"],
        )
        Aggregated = BuildMandatoryPortalTupleSelfConflictFailure((
            MandatoryPortalTupleSelfConflictEvidence(
                Signal="Second",
                CompletePortalTupleCount=1,
                EvaluatedPortalTupleCount=1,
                TerminalPortalDomainCounts=(1, 1),
                ConflictResources=(),
            ),
            MandatoryPortalTupleSelfConflictEvidence(
                Signal="First",
                CompletePortalTupleCount=1,
                EvaluatedPortalTupleCount=1,
                TerminalPortalDomainCounts=(1, 1, 1),
                ConflictResources=(),
            ),
        ))
        self.assertEqual(
            Aggregated.AffectedNets,
            ("First", "Second"),
        )
        self.assertEqual(
            Aggregated.Diagnostics["MandatoryAccessProof"][
                "SignalCount"
            ],
            2,
        )
        self.assertEqual(
            Aggregated.Diagnostics["ConflictGraph"][
                "CandidateCounts"
            ],
            {"First": 0, "Second": 0},
        )
        with self.assertRaises(ValueError):
            MandatoryPortalTupleSelfConflictEvidence(
                Signal="Incomplete",
                CompletePortalTupleCount=2,
                EvaluatedPortalTupleCount=1,
                TerminalPortalDomainCounts=(1, 2),
                ConflictResources=(),
            )

    def testOptionalPortalSeedSliceIsCappedAndRemainingAware(
        self,
    ) -> None:
        self.assertEqual(
            SelectOptionalPortalSeedSliceSeconds(8.0),
            0.5,
        )
        self.assertEqual(
            SelectOptionalPortalSeedSliceSeconds(1.0),
            0.25,
        )
        self.assertEqual(
            SelectOptionalPortalSeedSliceSeconds(0.0),
            0.0,
        )
        with self.assertRaises(ValueError):
            SelectOptionalPortalSeedSliceSeconds(
                8.0,
                MaximumSliceSeconds=0.0,
            )

    def testOptionalPortalSeedLocalExpiryPreservesSharedCheck(
        self,
    ) -> None:
        SharedChecks = []
        with patch(
            'Compiler.Routing.Authoritative.CandidateDomains.monotonic',
            return_value=10.0,
        ):
            WorkCheck = BuildOptionalPortalSeedWorkCheck(
                9.5,
                lambda Details: SharedChecks.append(dict(Details)),
            )
            with self.assertRaises(OptionalPortalSeedSliceExpired):
                WorkCheck({"Phase": "matching", "Signal": "RenamedSignal"})

        self.assertEqual(
            SharedChecks,
            [{"Phase": "matching", "Signal": "RenamedSignal"}],
        )

    def testOptionalPortalSeedSelectionIsNameIndependent(
        self,
    ) -> None:
        OriginalSignals = [f"Signal{Index}" for Index in range(9)]
        RenamedSignals = [
            f"Arbitrary{Index * 17}"
            for Index in reversed(range(9))
        ]

        self.assertEqual(
            ShouldPrepareOptionalPortalSeed(
                True,
                OriginalSignals,
                False,
            ),
            ShouldPrepareOptionalPortalSeed(
                True,
                RenamedSignals,
                False,
            ),
        )
        self.assertTrue(
            ShouldPrepareOptionalPortalSeed(
                True,
                OriginalSignals,
                False,
            )
        )
        self.assertFalse(
            ShouldPrepareOptionalPortalSeed(
                False,
                OriginalSignals,
                False,
            )
        )
        self.assertFalse(
            ShouldPrepareOptionalPortalSeed(
                True,
                OriginalSignals,
                True,
            )
        )

    def testPortalCoverageRegenerationTargetsOnlyReportedEndpoints(
        self,
    ) -> None:
        self.assertFalse(
            ShouldFreezePartialAssignmentForExactCut(
                "portal-coverage-pair-conflict",
                43,
                45,
                True,
            )
        )
        self.assertTrue(
            ShouldFreezePartialAssignmentForExactCut(
                "relocated-higher-order-conflict",
                43,
                45,
                True,
            )
        )
        self.assertTrue(
            ShouldFreezePartialAssignmentForExactCut(
                "relocated-higher-order-conflict",
                1,
                45,
                False,
            )
        )

    def testMandatoryPortalFailurePreservesEveryExactPairCut(self) -> None:
        Failure = BuildUnavoidableMandatoryClaimCutFailure(
            (
                (("B", "C"), frozenset({(2, 1, 0)})),
                (("A", "B"), frozenset({(1, 1, 0)})),
            ),
            {"PortalGeneration": 1.25},
        )

        self.assertEqual(Failure.AffectedNets, ("A", "B", "C"))
        self.assertEqual(
            Failure.Diagnostics["MandatoryConflictPairCount"],
            2,
        )
        self.assertEqual(
            Failure.Diagnostics["MandatoryConflictPositionCount"],
            2,
        )
        self.assertEqual(
            Failure.Diagnostics["StageTimingsSeconds"],
            {"PortalGeneration": 1.25},
        )
        self.assertEqual(
            Failure.Diagnostics["MandatoryAccessProof"]["Kind"],
            "generated-fixed-portal-domain-exhausted",
        )
        self.assertTrue(
            Failure.Diagnostics["MandatoryAccessProof"]["Complete"]
        )
        self.assertTrue(
            Failure.Diagnostics["MandatoryAccessProof"][
                "PortalTupleDomainComplete"
            ]
        )
        self.assertEqual(
            Failure.Diagnostics["MandatoryAccessProof"]["ProofScope"],
            "complete-portal-tuple-domain",
        )
        self.assertEqual(
            Failure.Diagnostics["ConflictGraph"][
                "PairwiseIncompatibleEdges"
            ],
            [["A", "B"], ["B", "C"]],
        )
        Cut = RoutingAssignmentCut.FromFailure(Failure)
        self.assertIsNotNone(Cut)
        self.assertEqual(
            Cut.PairwiseConflictEdges,
            (("A", "B"), ("B", "C")),
        )
        Constraints = PlacementAssignmentConstraintSet().WithCut(Cut)
        self.assertEqual(
            Constraints.PairwiseConflictEdges,
            (("A", "B"), ("B", "C")),
        )

    def testPortalTupleCompletenessRequiresEveryEligibleLayer(self) -> None:
        CompleteLayer = {
            "Layer": 0,
            "CompletePortalTupleCount": 16,
            "EvaluatedPortalTupleCount": 16,
            "PortalTupleDomainComplete": True,
        }
        self.assertFalse(PortalTupleFeasibilityDomainIsComplete(
            (CompleteLayer,),
            ExpectedLayers=range(2),
        ))
        self.assertTrue(PortalTupleFeasibilityDomainIsComplete(
            (
                CompleteLayer,
                {**CompleteLayer, "Layer": 1},
            ),
            ExpectedLayers=range(2),
        ))

    def testPortalBatchCompletionMaskRequiresCandidateAlignment(self) -> None:
        with self.assertRaises(ValueError):
            ReadPortalBatchCandidatesAndCompletionMask(
                SimpleNamespace(
                    Candidates=((),),
                    CompletedWork=1,
                    DeadlineExceeded=False,
                    CompletionMask=(True, False),
                ),
                2,
            )
        Candidates, CompletionMask = (
            ReadPortalBatchCandidatesAndCompletionMask(
                SimpleNamespace(
                    Candidates=((), ()),
                    CompletedWork=2,
                    DeadlineExceeded=False,
                    CompletionMask=(True, True),
                ),
                2,
            )
        )
        self.assertEqual(len(Candidates), 2)
        self.assertEqual(CompletionMask, (True, True))

        with self.assertRaises(ValueError):
            ReadPortalBatchCandidatesAndCompletionMask(
                SimpleNamespace(
                    Candidates=((), ()),
                    CompletedWork=1,
                    TotalWork=3,
                    DeadlineExceeded=True,
                    CompletionMask=(False, True),
                ),
                2,
            )

    def testConfiguredPortalRequestDomainBindsEverySearchInput(self) -> None:
        BaseArguments = (
            "SignalA",
            4,
            1000,
            "guide-input",
            (0, 20, -4, 16),
        )
        FirstRecord = (
            ((1, 2, 3), 0, ((1, 2, 3),), ((4, 2, 3),), "allowed-a", 2, 4, 1000),
        )
        Baseline = BuildConfiguredPortalRequestDomainFingerprint(
            *BaseArguments,
            FirstRecord,
        )
        self.assertNotEqual(
            Baseline,
            BuildConfiguredPortalRequestDomainFingerprint(
                *BaseArguments,
                (
                    ((1, 2, 3), 0, ((1, 2, 3),), ((5, 2, 3),), "allowed-a", 2, 4, 1000),
                ),
            ),
        )
        self.assertNotEqual(
            Baseline,
            BuildConfiguredPortalRequestDomainFingerprint(
                "SignalA",
                4,
                1001,
                "guide-input",
                (0, 20, -4, 16),
                FirstRecord,
            ),
        )

    def testFrozenPostClosurePortalHandoffReturnsExactPreparedFabric(
        self,
    ) -> None:
        Region = SimpleNamespace(
            Bounds=(0, 8, 1, 7, -2, 6),
            Nodes=((0, 2, 0), (1, 2, 0)),
            Edges=(((0, 2, 0), (1, 2, 0)),),
        )
        Graph = SimpleNamespace(GraphVersion="resource-graph-v1")
        RegionFingerprint = "exterior-region"
        ResourceFingerprint = BuildPhysicalExteriorResourceGraphFingerprint(
            Graph,
            RegionFingerprint,
            Region,
        )
        Resources = RoutingResources(
            RoutingStaticGeometry(frozenset(), frozenset()),
            ResourceGraph=Graph,
        )
        Cache = RawPortalGeometryCache(
            PlacementGeometryFingerprint="placement-geometry",
            ResourceGeometryFingerprint="resource-geometry",
            PlacedReference=object(),
            ResourcesReference=Resources,
            Region=Region,
            LayerCount=2,
            PortalLimit=6,
            PortalVariantCounts=(),
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            Context=object(),
            AssignmentIndexed=IndexedRoutingResourceGraph(
                ResourcePositions=(),
                PositionIndices={},
            ),
            PortalEntries=(),
            RequestCount=0,
            TargetCount=0,
            StarvationCount=0,
            AssignedColumns=frozenset({(0, 0), (1, 0)}),
            ReservedAccess=frozenset({(0, 2, 0)}),
            ExteriorRegionFingerprint=RegionFingerprint,
            AuthoritativeResourceGraphFingerprint=ResourceFingerprint,
        )
        Preparation = SimpleNamespace(
            DomainFingerprint="prepared-domain",
            PlacementFingerprint="placement",
            ComponentGraphFingerprint="component-graph",
            ResourceGraphFingerprint=ResourceFingerprint,
            ExteriorRegionFingerprint=RegionFingerprint,
            Complete=True,
        )
        Plan = SimpleNamespace(
            PlanFingerprint="assembly-plan",
            PlacementFingerprint="placement",
            ComponentGraphFingerprint="component-graph",
            ResourceGraphFingerprint=ResourceFingerprint,
            ExteriorRegionFingerprint=RegionFingerprint,
        )
        Resources.FrozenPhysicalComponentPostClosurePortalHandoff = (
            FrozenPhysicalComponentPostClosurePortalHandoff(
                PreparationDomainFingerprint="prepared-domain",
                PlacementFingerprint="placement",
                ComponentGraphFingerprint="component-graph",
                ResourceGraphFingerprint=ResourceFingerprint,
                ExteriorRegionFingerprint=RegionFingerprint,
                RawPortalGeometryCache=Cache,
            )
        )

        Selected = ValidateFrozenPhysicalComponentPostClosurePortalHandoff(
            Resources,
            Preparation,
            Plan,
        )

        self.assertIs(Selected, Cache)
        self.assertIs(Selected.Region, Region)
        self.assertEqual(Selected.AssignedColumns, Cache.AssignedColumns)
        self.assertEqual(Selected.ReservedAccess, Cache.ReservedAccess)
        self.assertIs(Selected.PortalEntries, Cache.PortalEntries)
        self.assertEqual(
            BuildFrozenPostClosurePortalHandoffTelemetry(
                Resources,
                Preparation,
                Plan,
            ),
            {
                "Applied": True,
                "PreparationDomainFingerprint": "prepared-domain",
                "PhysicalAssemblyPlanFingerprint": "assembly-plan",
                "ExteriorRegionFingerprint": RegionFingerprint,
                "AssignedColumnCount": 2,
                "ReservedAccessCount": 1,
                "PortalEntryCount": 0,
                "PortableProofUsed": False,
            },
        )

    def testMandatoryPortalPairFactorFindsCompleteWitness(self) -> None:
        Graph, Fixed, Domains = self._BuildMandatoryPortalPairFixture(
            ((0, 1, 0), (2, 1, 0))
        )
        Certificate = SolveMandatoryPortalPairFeasibility(
            Signals=("Alpha", "Beta"),
            FixedAccessNodesBySignal=Fixed,
            PortalDomainsBySignal=Domains,
            FrozenComponentClaims=(),
            ResourceGraph=Graph,
            DomainFingerprint="feasible-domain",
        )

        self.assertTrue(Certificate.Complete)
        self.assertTrue(Certificate.Feasible)
        self.assertEqual(
            dict(Certificate.WitnessPortalIds)["Alpha"],
            ("Alpha:1",),
        )
        self.assertEqual(SelectCertifiedMandatoryPortalPairCuts((Certificate,)), ())

    def testMandatoryPortalPairFactorProvesCompleteUnsat(self) -> None:
        Graph, Fixed, Domains = self._BuildMandatoryPortalPairFixture(
            ((0, 1, 0),)
        )
        Certificate = SolveMandatoryPortalPairFeasibility(
            Signals=("Alpha", "Beta"),
            FixedAccessNodesBySignal=Fixed,
            PortalDomainsBySignal=Domains,
            FrozenComponentClaims=(),
            ResourceGraph=Graph,
            DomainFingerprint="unsat-domain",
        )

        self.assertTrue(Certificate.Complete)
        self.assertFalse(Certificate.Feasible)
        self.assertTrue(Certificate.ConflictFingerprint)
        self.assertEqual(
            SelectCertifiedMandatoryPortalPairCuts((Certificate,))[0][0],
            ("Alpha", "Beta"),
        )

    def testMandatoryPortalPairFactorCompletesMultiTerminalProductWithMemo(
        self,
    ) -> None:
        Graph, Fixed, BaseDomains = self._BuildMandatoryPortalPairFixture(
            ((0, 1, 0),)
        )

        def At(Portal, PortalId, Position):
            return replace(
                Portal,
                PortalId=PortalId,
                Terminal=Position,
                Path=(Position,),
                Claims=Graph.BuildRouteClaims((Position,)),
            )

        Alpha = BaseDomains["Alpha"][0][0]
        Beta = BaseDomains["Beta"][0][0]
        Domains = {
            "Alpha": (
                (
                    At(Alpha, "Alpha:a0", (0, 1, 0)),
                    At(Alpha, "Alpha:a0-alias", (0, 1, 0)),
                ),
                (
                    At(Alpha, "Alpha:a1", (2, 1, 0)),
                    At(Alpha, "Alpha:a1-alias", (2, 1, 0)),
                ),
            ),
            "Beta": (
                (
                    At(Beta, "Beta:b0", (0, 1, 0)),
                    At(Beta, "Beta:b0-alias", (0, 1, 0)),
                ),
                (
                    At(Beta, "Beta:b1", (3, 1, 0)),
                    At(Beta, "Beta:b1-alias", (3, 1, 0)),
                ),
            ),
        }
        Certificate = SolveMandatoryPortalPairFeasibility(
            Signals=("Alpha", "Beta"),
            FixedAccessNodesBySignal=Fixed,
            PortalDomainsBySignal=Domains,
            FrozenComponentClaims=(),
            ResourceGraph=Graph,
            DomainFingerprint="multi-terminal-unsat-domain",
        )

        self.assertTrue(Certificate.Complete)
        self.assertFalse(Certificate.Feasible)
        self.assertGreater(Certificate.MemoizedStateHitCount, 0)
        self.assertLess(Certificate.ExpansionCount, 2 ** 4)
        self.assertEqual(
            SelectCertifiedMandatoryPortalPairCuts((Certificate,))[0][0],
            ("Alpha", "Beta"),
        )

    def testMandatoryPortalPairFactorReusesCompleteCertificate(self) -> None:
        Graph, Fixed, Domains = self._BuildMandatoryPortalPairFixture(
            ((0, 1, 0),)
        )
        Cache = {}
        Arguments = dict(
            Signals=("Alpha", "Beta"),
            FixedAccessNodesBySignal=Fixed,
            PortalDomainsBySignal=Domains,
            FrozenComponentClaims=(),
            ResourceGraph=Graph,
            DomainFingerprint="cached-domain",
        )
        First, FirstHit = GetMandatoryPortalPairFeasibilityCertificate(
            Cache,
            **Arguments,
        )
        Second, SecondHit = GetMandatoryPortalPairFeasibilityCertificate(
            Cache,
            **Arguments,
            ShouldStop=lambda: True,
        )

        self.assertFalse(FirstHit)
        self.assertTrue(SecondHit)
        self.assertIs(Second, First)

    def testBoundaryMandatoryPortalPairRelationCompilesFullTwoByTwoDomain(
        self,
    ) -> None:
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture()
        )
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Beta", "Alpha"),
            Resources,
        )

        self.assertTrue(Relation.Complete)
        self.assertEqual(Relation.ExpectedOptionPairCount, 4)
        self.assertEqual(len(Relation.Certificates), 4)
        self.assertEqual(len(Relation.UnsatisfiableApertureClauses), 3)
        self.assertEqual(
            sum(
                Value.Certificate.Feasible is True
                for Value in Relation.Certificates
            ),
            1,
        )

    def testBoundaryMandatoryPortalPairRelationMatchesExhaustiveReference(
        self,
    ) -> None:
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture()
        )
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
        )
        FactorsByAperture = {
            (Value.Signal, Value.ApertureContractFingerprint): Value
            for Value in Resources
            .PhysicalBoundaryMandatoryPortalFactorDomainCache.values()
        }
        for OptionCertificate in Relation.Certificates:
            First = FactorsByAperture[(
                Relation.Signals[0],
                OptionCertificate.FirstApertureContractFingerprint,
            )]
            Second = FactorsByAperture[(
                Relation.Signals[1],
                OptionCertificate.SecondApertureContractFingerprint,
            )]
            Reference = SolveMandatoryPortalPairFeasibility(
                Signals=Relation.Signals,
                FixedAccessNodesBySignal={
                    First.Signal: First.FixedAccessNodes,
                    Second.Signal: Second.FixedAccessNodes,
                },
                PortalDomainsBySignal={
                    First.Signal: First.PortalDomains,
                    Second.Signal: Second.PortalDomains,
                },
                FrozenComponentClaims=First.FrozenComponentClaims,
                ResourceGraph=Resources.ResourceGraph,
                DomainFingerprint=(
                    OptionCertificate.Certificate.DomainFingerprint
                ),
            )
            with self.subTest(
                First=First.ApertureContractFingerprint,
                Second=Second.ApertureContractFingerprint,
            ):
                self.assertTrue(Reference.Complete)
                self.assertEqual(
                    OptionCertificate.Certificate.Feasible,
                    Reference.Feasible,
                )
                if Reference.Feasible:
                    self.assertTrue(
                        OptionCertificate.Certificate.WitnessPortalIds
                    )

    def testBoundaryMandatoryPortalPairRelationUsesSparseExactProjection(
        self,
    ) -> None:
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture()
        )
        Resources.ResourceGraph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
        )

        self.assertTrue(Relation.Complete)
        self.assertEqual(Relation.ExpectedOptionPairCount, 4)
        self.assertEqual(Relation.FactorCertificateCount, 2)
        self.assertEqual(
            len(Relation.Certificates),
            Relation.ExpectedOptionPairCount,
        )
        self.assertEqual(
            [Value.Certificate.Feasible for Value in Relation.Certificates],
            [False, False, False, True],
        )

    def testBoundaryMandatoryPortalPairRelationMatchesSelfConflictReference(
        self,
    ) -> None:
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture()
        )

        class SelfConflictGraph:
            @staticmethod
            def BuildRouteClaims(Nodes):
                Nodes = frozenset(Nodes)
                return RoutingResourceClaims(
                    WireCells=Nodes,
                    SupportCells=Nodes,
                    ElectricalCells=Nodes,
                )

        Resources.ResourceGraph = SelfConflictGraph()
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
        )

        self.assertTrue(Relation.Complete)
        self.assertTrue(all(
            Value.Certificate.Feasible is False
            for Value in Relation.Certificates
        ))
        self.assertEqual(
            len(Relation.UnsatisfiableApertureClauses),
            Relation.ExpectedOptionPairCount,
        )

    def testBoundaryMandatoryPortalPairRelationMatchesFrozenBlockerReference(
        self,
    ) -> None:
        Graph, _Fixed, _Domains = self._BuildMandatoryPortalPairFixture(())
        Position = (0, 1, 0)
        ForeignClaim = LocalRouteClaim(
            Signal="Foreign",
            ClusterId=0,
            Root=Position,
            ConnectedTargets=(),
            BoundaryNodes=(),
            Nodes=frozenset((Position,)),
            Edges=frozenset(),
            Claims=Graph.BuildRouteClaims((Position,)),
        )
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture(
                FrozenClaims=(ForeignClaim,),
            )
        )
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
        )

        self.assertTrue(Relation.Complete)
        self.assertGreater(Relation.ForeignDependencyCertificateCount, 0)
        self.assertTrue(any(
            "Foreign" in Value.Certificate.DependencySignals
            for Value in Relation.Certificates
        ))
        FactorsByAperture = {
            (Value.Signal, Value.ApertureContractFingerprint): Value
            for Value in Resources
            .PhysicalBoundaryMandatoryPortalFactorDomainCache.values()
        }
        for Value in Relation.Certificates:
            First = FactorsByAperture[(
                Relation.Signals[0],
                Value.FirstApertureContractFingerprint,
            )]
            Second = FactorsByAperture[(
                Relation.Signals[1],
                Value.SecondApertureContractFingerprint,
            )]
            Reference = SolveMandatoryPortalPairFeasibility(
                Signals=Relation.Signals,
                FixedAccessNodesBySignal={
                    First.Signal: First.FixedAccessNodes,
                    Second.Signal: Second.FixedAccessNodes,
                },
                PortalDomainsBySignal={
                    First.Signal: First.PortalDomains,
                    Second.Signal: Second.PortalDomains,
                },
                FrozenComponentClaims=First.FrozenComponentClaims,
                ResourceGraph=Resources.ResourceGraph,
                DomainFingerprint=Value.Certificate.DomainFingerprint,
            )
            self.assertEqual(Value.Certificate.Feasible, Reference.Feasible)
        self.assertFalse(any(
            frozenset(Value.Certificate.DependencySignals)
            <= frozenset(Relation.Signals)
            and Value.Certificate.Feasible is False
            and frozenset((
                (
                    Relation.Signals[0],
                    Value.FirstApertureContractFingerprint,
                ),
                (
                    Relation.Signals[1],
                    Value.SecondApertureContractFingerprint,
                ),
            )) not in Relation.UnsatisfiableApertureClauses
            for Value in Relation.Certificates
        ))

    def testBoundaryMandatoryPortalPairRelationDoesNotProjectForeignDependency(
        self,
    ) -> None:
        Graph, _Fixed, _Domains = self._BuildMandatoryPortalPairFixture(())
        Position = (0, 1, 0)
        Claims = Graph.BuildRouteClaims((Position,))
        ForeignClaim = LocalRouteClaim(
            Signal="Foreign",
            ClusterId=0,
            Root=Position,
            ConnectedTargets=(),
            BoundaryNodes=(),
            Nodes=frozenset((Position,)),
            Edges=frozenset(),
            Claims=Claims,
        )
        Preparation, Resources = (
            self._BuildBoundaryMandatoryPairRelationFixture(
                FrozenClaims=(ForeignClaim,),
            )
        )
        Relation = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            Preparation,
            ("Alpha", "Beta"),
            Resources,
        )

        self.assertTrue(Relation.Complete)
        self.assertGreater(Relation.ForeignDependencyCertificateCount, 0)
        self.assertLess(
            len(Relation.UnsatisfiableApertureClauses),
            3,
        )

    def testBoundaryMandatoryPortalPairRelationIsOrderAndRenameInvariant(
        self,
    ) -> None:
        FirstPreparation, FirstResources = (
            self._BuildBoundaryMandatoryPairRelationFixture()
        )
        SecondPreparation, SecondResources = (
            self._BuildBoundaryMandatoryPairRelationFixture(
                SignalNames=("Left", "Right"),
                ReverseDomains=True,
            )
        )
        First = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            FirstPreparation,
            ("Beta", "Alpha"),
            FirstResources,
        )
        Second = CompilePhysicalBoundaryMandatoryPortalPairRelation(
            SecondPreparation,
            ("Right", "Left"),
            SecondResources,
        )

        self.assertEqual(
            sorted(len(Value) for Value in First.UnsatisfiableApertureClauses),
            sorted(len(Value) for Value in Second.UnsatisfiableApertureClauses),
        )
        self.assertEqual(
            [Value.Certificate.Feasible for Value in First.Certificates],
            [Value.Certificate.Feasible for Value in Second.Certificates],
        )

    def testBoundaryPortalFactorSeparatesCommonAndOptionOverlayProvenance(
        self,
    ) -> None:
        _Preparation, _Resources, RootCoveredDomains = (
            self._BuildPreparedBoundaryPortalFactorFixture(
                ((0, 1, 0), (2, 1, 0)),
            )
        )
        _Preparation, _Resources, RootExternalDomains = (
            self._BuildPreparedBoundaryPortalFactorFixture(
                ((10, 1, 0), (8, 1, 0)),
                AlphaAttachment=(10, 1, 0),
                AlphaOwnedTerminal=(10, 1, 0),
            )
        )
        RootCovered = next(
            Value for Value in RootCoveredDomains
            if Value.Signal == "Alpha"
        )
        RootExternal = next(
            Value for Value in RootExternalDomains
            if Value.Signal == "Alpha"
        )

        self.assertEqual(
            RootCovered.CommonFixedAccessNodes,
            frozenset(((10, 1, 0),)),
        )
        self.assertEqual(
            RootCovered.OptionOverlayNodes,
            frozenset(((0, 1, 0), (2, 1, 0))),
        )
        self.assertEqual(
            RootExternal.CommonFixedAccessNodes,
            frozenset(),
        )
        self.assertEqual(
            RootExternal.OptionOverlayNodes,
            frozenset(((10, 1, 0), (8, 1, 0))),
        )
        self.assertEqual(
            RootCovered.FixedAccessNodes,
            RootCovered.CommonFixedAccessNodes
            | RootCovered.OptionOverlayNodes,
        )
        self.assertEqual(
            RootExternal.FixedAccessNodes,
            RootExternal.CommonFixedAccessNodes
            | RootExternal.OptionOverlayNodes,
        )

    def testBoundaryPortalFactorRequiresAttachmentProfileTerminal(self) -> None:
        _Preparation, _Resources, Domains = (
            self._BuildPreparedBoundaryPortalFactorFixture(
                ((8, 1, 0), (2, 1, 0)),
                AlphaAttachment=(8, 1, 0),
                AlphaHasExternalTarget=False,
            )
        )

        self.assertFalse(any(
            Value.Signal == "Alpha" for Value in Domains
        ))

    def testBoundaryPortalRegionMismatchCannotPublishCompleteFactor(self) -> None:
        _Preparation, _Resources, Domains = (
            self._BuildPreparedBoundaryPortalFactorFixture(
                ((0, 1, 0), (0, 1, 2)),
                ExteriorRegionFingerprint="different-region",
            )
        )
        self.assertTrue(Domains)
        self.assertTrue(all(not Value.Complete for Value in Domains))

    def testMandatoryPortalFailureBatchIsOrderIndependent(self) -> None:
        First = BuildUnavoidableMandatoryClaimCutFailure((
            (("Left", "Center"), frozenset({(1, 1, 0)})),
            (("Center", "Right"), frozenset({(2, 1, 0)})),
        ))
        Reordered = BuildUnavoidableMandatoryClaimCutFailure((
            (("Right", "Center"), frozenset({(2, 1, 0)})),
            (("Center", "Left"), frozenset({(1, 1, 0)})),
        ))

        self.assertEqual(
            First.Diagnostics["ConflictGraph"],
            Reordered.Diagnostics["ConflictGraph"],
        )
        self.assertEqual(First.AffectedNets, Reordered.AffectedNets)
        self.assertEqual(First.Locations, Reordered.Locations)
        Translated = BuildUnavoidableMandatoryClaimCutFailure((
            (("RenamedA", "RenamedB"), frozenset({(101, 8, 40)})),
            (("RenamedB", "RenamedC"), frozenset({(102, 8, 40)})),
        ))
        self.assertEqual(
            First.Diagnostics["MandatoryAccessProof"][
                "ConflictFingerprint"
            ],
            Translated.Diagnostics["MandatoryAccessProof"][
                "ConflictFingerprint"
            ],
        )

    def testCandidateRetriesStartAtUnseenPortalShapes(self) -> None:
        self.assertEqual(CandidateRequestWindowOffset(8, 2, 8, 0), 0)
        self.assertEqual(CandidateRequestWindowOffset(8, 2, 8, 1), 1)
        self.assertEqual(CandidateRequestWindowOffset(8, 2, 8, 2), 3)

        OrderedVariants = sorted(
            range(6),
            key=lambda Variant: CandidatePortalShapeRank(
                Variant,
                AxisIndex=0,
                LaneIndex=0,
                LayerIndex=0,
                PortalVariantCount=6,
                LaneCount=1,
                RequestWindowOffset=3,
            ),
        )
        self.assertEqual(OrderedVariants, [3, 4, 5, 0, 1, 2])

    def testPhysicalPortDecompositionDeduplicatesSharedAperture(self) -> None:
        def Claims(Nodes):
            Values = frozenset(Nodes)
            return RoutingResourceClaims(
                WireCells=Values,
                ElectricalCells=Values,
            )

        GlobalPath = ((4, 1, 0), (5, 1, 0))
        Seams = tuple(
            PhysicalPortSeamFactor(
                FabricAttachment=(0, 1, 0),
                Attachment=(4, 1, 0),
                LocalPath=((0, 1, 0), (LocalX, 1, 0)),
                GlobalPath=GlobalPath,
                Claims=Claims(((0, 1, 0), (LocalX, 1, 0), *GlobalPath)),
                SeamFingerprint=f"seam-{LocalX}",
            )
            for LocalX in (1, 2)
        )
        LaneFactors = {"sum": tuple(
            PhysicalPortLaneFactor(
                Signal="sum",
                Direction="output",
                Capacity=1,
                OwnedTerminals=((0, 1, 0),),
                Domains=(),
                CandidateDomains=(),
                FabricDomainFingerprint=f"fabric-{Index}",
                Seams=(Seam,),
                GuideCells=frozenset(((4, 0), (5, 0))),
                ExternalTerminals=((8, 1, 0),),
            )
            for Index, Seam in enumerate(Seams)
        )}
        Channel = PhysicalComponentChannelReservation(
            Signal="sum",
            Layer=0,
            GuideCells=((4, 0), (5, 0)),
            ResourceIds=(),
            Claims=Claims(GlobalPath),
            ReservationFingerprint="channel-sum",
        )
        ResourceGraph = SimpleNamespace(
            BuildRouteClaims=lambda Nodes: Claims(Nodes)
        )

        Local, Apertures, Supports = DecomposePhysicalPortLaneFactors(
            LaneFactors,
            (Channel,),
            ResourceGraph,
            FabricOrigin=(0, 1, 0),
        )

        self.assertEqual(len(Local[0][1]), 2)
        self.assertEqual(len(Apertures[0][1]), 1)
        self.assertEqual(len(Supports[0][1]), 2)
        self.assertEqual(
            {Value.ApertureOptionFingerprint for Value in Supports[0][1]},
            {Apertures[0][1][0].ApertureOptionFingerprint},
        )
        LocalByFingerprint = {
            Value.LocalAccessFingerprint: Value
            for Value in Local[0][1]
        }
        ApertureByFingerprint = {
            Value.ApertureOptionFingerprint: Value
            for Value in Apertures[0][1]
        }
        for Support in Supports[0][1]:
            Port = MaterializeSupportedPhysicalPortReservation(
                LocalByFingerprint[Support.LocalAccessFingerprint],
                ApertureByFingerprint[
                    Support.ApertureOptionFingerprint
                ],
                Support,
                ResourceGraph,
            )
            self.assertEqual(
                Port.ReservationFingerprint,
                Support.ReservationFingerprint,
            )
            self.assertEqual(
                Port.Claims,
                ResourceGraph.BuildRouteClaims(frozenset((
                    *Port.LocalPath,
                    *Port.GlobalPath,
                ))),
            )

    def testPreparedExteriorGuideFabricUnionsEveryLegalSeam(self) -> None:
        Preparation = SimpleNamespace(
            Complete=True,
            LaneFactorsBySignal=((
                "PortA",
                (
                    SimpleNamespace(
                        GuideCells=frozenset(((0, 0), (1, 0))),
                        Seams=(SimpleNamespace(
                            GlobalPath=((1, 2, 0), (2, 2, 0)),
                        ),),
                    ),
                    SimpleNamespace(
                        GuideCells=frozenset(((0, 1),)),
                        Seams=(SimpleNamespace(
                            GlobalPath=((1, 2, 1), (2, 2, 1)),
                        ),),
                    ),
                ),
            ),),
        )

        Expected = {"PortA": frozenset({
            (0, 0), (1, 0), (2, 0),
            (0, 1), (1, 1), (2, 1),
        })}
        self.assertEqual(
            BuildPreparedPhysicalExteriorGuideColumnsBySignal(Preparation),
            Expected,
        )
        ReorderedPreparation = SimpleNamespace(
            Complete=True,
            LaneFactorsBySignal=(
                ("PortA", tuple(reversed(
                    Preparation.LaneFactorsBySignal[0][1]
                ))),
            ),
        )
        self.assertEqual(
            BuildPreparedPhysicalExteriorGuideColumnsBySignal(
                ReorderedPreparation
            ),
            Expected,
        )

    def testCoordinatedReservedPortalTupleUsesTargetedWindowOffset(
        self,
    ) -> None:
        self.assertEqual(
            CandidatePortalTupleIndex(
                Variant=0,
                PortalPhase=1,
                PortalTupleCount=6,
            ),
            1,
        )
        self.assertEqual(
            CandidatePortalTupleIndex(
                Variant=0,
                PortalPhase=1,
                PortalTupleCount=6,
                CoordinatedRequestWindowOffset=1,
            ),
            2,
        )
        self.assertEqual(
            CandidatePortalTupleIndex(
                Variant=5,
                PortalPhase=3,
                PortalTupleCount=6,
                CoordinatedRequestWindowOffset=1,
            ),
            3,
        )
        with self.assertRaises(ValueError):
            CandidatePortalTupleIndex(0, 0, 0)
        with self.assertRaises(ValueError):
            CandidatePortalTupleIndex(0, 0, 1, -1)

    def testCandidatePortalShapesUseEveryStartBeforeAlternateAxis(self) -> None:
        OrderedShapes = sorted(
            (
                (Variant, AxisIndex)
                for AxisIndex in range(2)
                for Variant in range(3)
            ),
            key=lambda Value: CandidatePortalShapeRank(
                Value[0],
                Value[1],
                LaneIndex=0,
                LayerIndex=0,
                PortalVariantCount=3,
                LaneCount=1,
                RequestWindowOffset=0,
            ),
        )
        self.assertEqual(
            OrderedShapes,
            [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)],
        )

        RankZeroShapesByLayer = []
        for LayerIndex in range(6):
            RankZeroShapesByLayer.append(next(
                (Variant, AxisIndex)
                for AxisIndex in range(2)
                for Variant in range(3)
                if CandidatePortalShapeRank(
                    Variant,
                    AxisIndex,
                    LaneIndex=0,
                    LayerIndex=LayerIndex,
                    PortalVariantCount=3,
                    LaneCount=1,
                    RequestWindowOffset=0,
                ) == 0
            ))
        self.assertEqual(
            RankZeroShapesByLayer,
            [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)],
        )

        OrderedLaneShapes = sorted(
            (
                (Variant, LaneIndex)
                for LaneIndex in range(2)
                for Variant in range(3)
            ),
            key=lambda Value: CandidatePortalShapeRank(
                Value[0],
                AxisIndex=0,
                LaneIndex=Value[1],
                LayerIndex=0,
                PortalVariantCount=3,
                LaneCount=2,
                RequestWindowOffset=0,
            ),
        )
        self.assertEqual(
            OrderedLaneShapes,
            [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)],
        )

    def testBoundaryPortalReservationUsesDisjointForeignSlots(self) -> None:
        First = self.BuildPortal("A", (0, 1, 0), (1, 1, 0))
        Second = self.BuildPortal("A", (0, 1, 0), (2, 1, 0))
        Foreign = self.BuildPortal("B", (4, 1, 0), (1, 1, 0))
        ForeignClear = self.BuildPortal("B", (4, 1, 0), (3, 1, 0))
        Reserved, Reservations = ReserveBoundaryPortals({
            ("A", (0, 1, 0), 0): (First, Second),
            ("B", (4, 1, 0), 0): (Foreign, ForeignClear),
        })
        self.assertEqual(Reservations[0].PortalId, First.PortalId)
        self.assertEqual(Reservations[1].PortalId, ForeignClear.PortalId)
        self.assertEqual(Reserved[("B", (4, 1, 0), 0)][0], ForeignClear)

    def testBoundaryPortalReservationSolvesScarceEscapeBeforeCheapEscape(self) -> None:
        # A naive signal-order allocator picks A's cheap first stem and makes
        # B impossible.  The escape allocator instead selects B's one legal
        # stem first, then moves A to its remaining compatible stem.
        First = self.BuildPortal("A", (0, 1, 0), (1, 1, 0))
        Alternate = self.BuildPortal("A", (0, 1, 0), (2, 1, 0))
        Scarce = self.BuildPortal("B", (4, 1, 0), (1, 1, 0))
        Reserved, _Reservations = ReserveBoundaryPortals({
            ("A", (0, 1, 0), 0): (First, Alternate),
            ("B", (4, 1, 0), 0): (Scarce,),
        })
        self.assertEqual(Reserved[("A", (0, 1, 0), 0)], (Alternate,))
        self.assertEqual(Reserved[("B", (4, 1, 0), 0)], (Scarce,))

    def testBoundaryPortalReservationDoesNotCrossReserveLayers(self) -> None:
        First = self.BuildPortal("A", (0, 1, 0), (1, 1, 0), Layer=0)
        Second = self.BuildPortal("B", (4, 1, 0), (1, 1, 0), Layer=1)
        Reserved, _Reservations = ReserveBoundaryPortals({
            ("A", (0, 1, 0), 0): (First,),
            ("B", (4, 1, 0), 1): (Second,),
        })
        self.assertEqual(Reserved[("A", (0, 1, 0), 0)], (First,))
        self.assertEqual(Reserved[("B", (4, 1, 0), 1)], (Second,))

    def testBoundaryPortalReservationAllowsLayerSpecificInaccessibility(self) -> None:
        Terminal = (0, 7, 0)
        Reachable = self.BuildPortal("A", Terminal, (2, 4, 0), Layer=1)

        Reserved, Reservations = ReserveBoundaryPortals({
            ("A", Terminal, 0): (),
            ("A", Terminal, 1): (Reachable,),
        })

        self.assertEqual(Reserved[("A", Terminal, 0)], ())
        self.assertEqual(Reserved[("A", Terminal, 1)], (Reachable,))
        self.assertEqual(tuple(Value.Layer for Value in Reservations), (1,))

    def testBoundaryPortalReservationRejectsTerminalWithoutAnyLayer(self) -> None:
        Terminal = (0, 7, 0)

        with self.assertRaisesRegex(
            RoutingStageError,
            "no boundary-portal geometry available on any layer",
        ) as Context:
            ReserveBoundaryPortals({
                ("A", Terminal, 0): (),
                ("A", Terminal, 1): (),
            })

        self.assertEqual(
            Context.exception.Failure.Reason,
            RoutingFailureReason.NoBoundaryEscape,
        )
        self.assertEqual(Context.exception.Failure.AffectedNets, ("A",))
        self.assertEqual(
            Context.exception.Failure.Diagnostics["Layers"],
            [0, 1],
        )

    def testNegotiatedReservationSelectsNetWideSelfLegalPortalTuple(self) -> None:
        Graph = RoutingResourceGraph(
            ActualBlocks=frozenset(),
            ElectricalBlocks=frozenset(),
            SolidBlocks=frozenset(),
        )
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=Graph,
        )
        Signal = "A"
        Root = (10, 1, 0)
        Target = (20, 1, 0)

        def Portal(Label, Terminal, Position):
            return PinAccessPortal(
                PortalId=Label,
                Signal=Signal,
                Terminal=Terminal,
                Layer=0,
                Path=(Position,),
                Edges=frozenset(),
                Claims=Graph.BuildRouteClaims((Position,)),
                Length=0,
                BendCount=0,
                ViaCount=0,
                Cost=0,
            )

        Source = Portal("source", Root, (0, 2, 0))
        ConflictingTarget = Portal("target-conflict", Target, (0, 1, 0))
        LegalTarget = Portal("target-legal", Target, (3, 1, 0))
        Profile = NetRoutingProfile(
            Signal=Signal,
            Root=Root,
            Targets=(Target,),
            Span=10,
            Fanout=1,
            RetryCount=0,
            Criticality=1,
            IsTrunk=False,
            SourceAccessPath=(Root,),
            TargetAccessPaths={Target: (Target,)},
        )

        Reserved, Reservations = ReserveNegotiatedBoundaryEscapes(
            {
                (Signal, Root, 0): (Source,),
                (Signal, Target, 0): (
                    ConflictingTarget,
                    LegalTarget,
                ),
            },
            {Signal: Profile},
            Resources,
        )

        self.assertEqual(
            Reserved[(Signal, Target, 0)],
            (LegalTarget,),
        )
        self.assertEqual(
            {Reservation.PortalId for Reservation in Reservations},
            {"source", "target-legal"},
        )

    def testClusterInterfaceJointCutRecoversPortalOmittedByPatternBeam(
        self,
    ) -> None:
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=RoutingResourceGraph(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
                SolidBlocks=frozenset(),
            ),
        )
        FirstTerminal = (100, 1, 0)
        SecondTerminal = (200, 1, 0)
        BlockedPositions = tuple(
            (Index, 1, 0) for Index in range(6)
        )
        SafePosition = (9, 1, 0)
        FirstPortals = tuple(
            self.BuildPortal(
                "First",
                FirstTerminal,
                Position,
            )
            for Position in (*BlockedPositions, SafePosition)
        )
        SecondPortal = PinAccessPortal(
            PortalId="Second:blocker",
            Signal="Second",
            Terminal=SecondTerminal,
            Layer=0,
            Path=BlockedPositions,
            Edges=frozenset(),
            Claims=RoutingResourceClaims(
                WireCells=frozenset(BlockedPositions)
            ),
            Length=len(BlockedPositions),
            BendCount=0,
            ViaCount=0,
            Cost=0,
        )

        def Profile(Signal, Root):
            return NetRoutingProfile(
                Signal=Signal,
                Root=Root,
                Targets=(),
                Span=0,
                Fanout=0,
                RetryCount=0,
                Criticality=1,
                IsTrunk=False,
                SourceAccessPath=(Root,),
                TargetAccessPaths={},
            )

        _Reserved, Reservations = ReserveClusterBoundaryLeases(
            {
                ("First", FirstTerminal, 0): FirstPortals,
                ("Second", SecondTerminal, 0): (SecondPortal,),
            },
            {
                "First": Profile("First", FirstTerminal),
                "Second": Profile("Second", SecondTerminal),
            },
            Resources,
            MaximumExpansions=1_000,
        )

        self.assertEqual(
            next(
                Reservation.FirstSegment
                for Reservation in Reservations
                if Reservation.Signal == "First"
            ),
            (SafePosition,),
        )

    def testClusterInterfaceJointCutRecoversHigherOrderOmittedPortal(
        self,
    ) -> None:
        Resources = RoutingResources(
            StaticGeometry=RoutingStaticGeometry(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
            ),
            ResourceGraph=RoutingResourceGraph(
                ActualBlocks=frozenset(),
                ElectricalBlocks=frozenset(),
                SolidBlocks=frozenset(),
            ),
        )
        Signals = ("First", "Second", "Third")
        Terminals = {
            Signal: (100 + 10 * Index, 1, 0)
            for Index, Signal in enumerate(Signals)
        }
        Colors = ((0, 1, 0), (4, 1, 0), (8, 1, 0))

        def Portal(Signal, Terminal, Index, Position):
            Claims = RoutingResourceClaims(
                WireCells=frozenset((Position,))
            )
            return PinAccessPortal(
                PortalId=f"{Signal}:{Index}",
                Signal=Signal,
                Terminal=Terminal,
                Layer=0,
                Path=(Position,),
                Edges=frozenset(),
                Claims=Claims,
                Length=0,
                BendCount=0,
                ViaCount=0,
                Cost=0,
            )

        def Profile(Signal):
            Root = Terminals[Signal]
            return NetRoutingProfile(
                Signal=Signal,
                Root=Root,
                Targets=(),
                Span=0,
                Fanout=0,
                RetryCount=0,
                Criticality=1,
                IsTrunk=False,
                SourceAccessPath=(Root,),
                TargetAccessPaths={},
            )

        _Reserved, Reservations = ReserveClusterBoundaryLeases(
            {
                (Signal, Terminals[Signal], 0): tuple(
                    Portal(
                        Signal,
                        Terminals[Signal],
                        Index,
                        Colors[Index % 2] if Index < 6 else Colors[2],
                    )
                    for Index in range(7)
                )
                for Signal in Signals
            },
            {Signal: Profile(Signal) for Signal in Signals},
            Resources,
            MaximumExpansions=2_000,
        )

        self.assertEqual(
            {
                Reservation.FirstSegment
                for Reservation in Reservations
            },
            {(Color,) for Color in Colors},
        )

    def testExactPhysicalAttachmentSkipsGenericPortalPreparation(
        self,
    ) -> None:
        Profile = SimpleNamespace(
            Signal="PortA",
            Root=(0, 7, 0),
            SourceAccessPath=((0, 7, 0), (1, 7, 0)),
            Targets=((8, 7, 0),),
            TargetAccessPaths={
                (8, 7, 0): ((8, 7, 0), (7, 7, 0)),
            },
        )
        Plan = SimpleNamespace(Ports=(SimpleNamespace(
            Signal="PortA",
            Attachment=(0, 7, 0),
        ),))

        self.assertEqual(
            SelectGenericPortalTerminalPaths(Profile, Plan),
            (((8, 7, 0), ((8, 7, 0), (7, 7, 0))),),
        )
        self.assertEqual(
            SelectGenericPortalTerminalPaths(Profile, None),
            (
                ((0, 7, 0), ((0, 7, 0), (1, 7, 0))),
                ((8, 7, 0), ((8, 7, 0), (7, 7, 0))),
            ),
        )

    def testReboundCandidatePortalRemainsInCurrentGlobalDomain(self) -> None:
        Attachment = (4, 3, 7)
        GlobalPath = (Attachment, (5, 3, 7))

        def Port(ReservationFingerprint, LocalPath):
            return SimpleNamespace(
                Signal="PortA",
                Direction="output",
                Attachment=Attachment,
                GlobalPath=GlobalPath,
                LocalPath=LocalPath,
                Capacity=1,
                ReservationFingerprint=ReservationFingerprint,
            )

        Channel = SimpleNamespace(Signal="PortA", Layer=1)
        First = Port("local-reservation-a", ((2, 3, 7), Attachment))
        LocalChanged = Port(
            "local-reservation-b",
            ((1, 3, 7), (2, 3, 7), Attachment),
        )

        def Plan(Value):
            return SimpleNamespace(
                Ports=(Value,),
                PlanningChannels=(Channel,),
            )

        ResourceGraph = SimpleNamespace(
            BuildRouteClaims=lambda Path: RoutingResourceClaims(
                WireCells=frozenset(Path),
            ),
        )
        FirstDomains = ApplyPhysicalComponentAssemblyPortalDomains(
            {}, Plan(First), ResourceGraph,
        )
        NextDomains = ApplyPhysicalComponentAssemblyPortalDomains(
            {}, Plan(LocalChanged), ResourceGraph,
        )
        FirstPortalId = next(iter(FirstDomains.values()))[0].PortalId
        self.assertEqual(
            FirstPortalId,
            next(iter(NextDomains.values()))[0].PortalId,
        )
        Candidate = SimpleNamespace(
            CandidateId="bound-global-candidate",
            SourcePortalId=FirstPortalId,
            TargetPortalIds={},
        )
        Retained, Removed = FilterPhysicalCandidatesToCurrentPortalDomain(
            {"PortA": (Candidate,)},
            NextDomains,
        )
        self.assertEqual(Retained, {"PortA": (Candidate,)})
        self.assertEqual(Removed, {})

    def testGreedyBoundaryPortalReservationVariantChangesPhysicalSlot(self) -> None:
        First = self.BuildPortal("A", (0, 1, 0), (1, 1, 0))
        Second = self.BuildPortal("A", (0, 1, 0), (2, 1, 0))
        Portals = {("A", (0, 1, 0), 0): (First, Second)}

        Initial, _InitialReservations = ReserveBoundaryPortals(
            Portals,
            ReservationVariant=0,
            RequireConflictFree=False,
        )
        Alternate, _AlternateReservations = ReserveBoundaryPortals(
            Portals,
            ReservationVariant=1,
            RequireConflictFree=False,
        )

        self.assertEqual(Initial[("A", (0, 1, 0), 0)], (First,))
        self.assertEqual(Alternate[("A", (0, 1, 0), 0)], (Second,))

    def testRawPortalProfileCannotOverrideWiderRetryControls(self) -> None:
        Cache = self.BuildRawPortalCache(
            object(),
            object(),
            {"Alpha": 2, "Beta": 2},
        )

        self.assertTrue(
            RawPortalProfileMatchesRequestedControls(
                Cache,
                6,
                {"Alpha": 2, "Beta": 2},
            )
        )
        self.assertFalse(
            RawPortalProfileMatchesRequestedControls(
                Cache,
                10,
                {"Alpha": 10, "Beta": 10},
            )
        )
        self.assertFalse(
            RawPortalProfileMatchesRequestedControls(
                Cache,
                6,
                {"Alpha": 2, "Beta": 6},
            )
        )
        self.assertFalse(
            RawPortalProfileMatchesRequestedControls(
                None,
                6,
                {"Alpha": 2, "Beta": 2},
            )
        )

    def testCertifiedApertureDomainKeepsSiblingChangesLocal(self) -> None:
        def Claims(*Nodes):
            NodeSet = frozenset(Nodes)
            return RoutingResourceClaims(
                WireCells=NodeSet,
                ElectricalCells=NodeSet,
            )

        def Port(Signal, Path, Fingerprint):
            return SimpleNamespace(
                Signal=Signal,
                Direction="output",
                Attachment=Path[0],
                GlobalPath=tuple(Path),
                Capacity=1,
                ReservationFingerprint=Fingerprint,
                Claims=Claims(*Path),
                GlobalClaims=Claims(*Path),
            )

        def Plan(BetaPath=((4, 2, 0), (5, 2, 0))):
            Ports = (
                Port("Alpha", ((0, 2, 0), (1, 2, 0)), "port-a"),
                Port("Beta", BetaPath, "port-b:" + str(BetaPath)),
            )
            return SimpleNamespace(
                Ports=Ports,
                PlanningChannels=tuple(
                    SimpleNamespace(
                        Signal=Value.Signal,
                        ReservationFingerprint="channel:" + Value.Signal,
                    )
                    for Value in Ports
                ),
                GlobalKeepoutNodes=frozenset({
                    (0, 2, 0), (1, 2, 0), (2, 2, 0), (3, 2, 0),
                }),
                ComponentGraphFingerprint="component",
                ResourceGraphFingerprint="resource",
                TechnologyFingerprint="technology",
            )

        First = BuildCertifiedPhysicalComponentApertureDomain(
            Plan(),
            Complete=True,
        )
        Second = BuildCertifiedPhysicalComponentApertureDomain(
            Plan(((4, 2, 1), (5, 2, 1))),
            Complete=True,
        )
        FirstIdentity = BuildPhysicalSignalApertureCandidateDomainIdentity(
            First,
            "Alpha",
            "request-alpha",
            ((2, 2, 0), (3, 2, 0)),
            CoverageCursor=7,
            Complete=False,
        )
        SecondIdentity = BuildPhysicalSignalApertureCandidateDomainIdentity(
            Second,
            "Alpha",
            "request-alpha",
            ((2, 2, 0), (3, 2, 0)),
            CoverageCursor=7,
            Complete=False,
        )

        self.assertTrue(First.Complete)
        self.assertEqual(
            First.StableKeepoutCoreFingerprint,
            Second.StableKeepoutCoreFingerprint,
        )
        self.assertNotEqual(
            First.DomainFingerprint,
            Second.DomainFingerprint,
        )
        self.assertEqual(
            FirstIdentity.DomainFingerprint,
            SecondIdentity.DomainFingerprint,
        )
        ChangedBlockedNodes = (
            BuildPhysicalSignalApertureCandidateDomainIdentity(
                First,
                "Alpha",
                "request-alpha",
                ((2, 2, 0),),
                CoverageCursor=7,
                Complete=False,
            )
        )
        self.assertNotEqual(
            FirstIdentity.DomainFingerprint,
            ChangedBlockedNodes.DomainFingerprint,
        )
        AdvancedIdentity = (
            BuildPhysicalSignalApertureCandidateDomainIdentity(
                First,
                "Alpha",
                "request-alpha",
                ((2, 2, 0), (3, 2, 0)),
                CoverageCursor=8,
                Complete=True,
            )
        )
        self.assertEqual(
            FirstIdentity.StableDomainFingerprint,
            AdvancedIdentity.StableDomainFingerprint,
        )
        self.assertNotEqual(
            FirstIdentity.DomainFingerprint,
            AdvancedIdentity.DomainFingerprint,
        )
        self.assertNotEqual(
            FirstIdentity.StableDomainFingerprint,
            ChangedBlockedNodes.StableDomainFingerprint,
        )

    def testExteriorRouteDomainComposesExactApertureAndChannel(self) -> None:
        def Claims(*Nodes):
            NodeSet = frozenset(Nodes)
            return RoutingResourceClaims(
                WireCells=NodeSet,
                ElectricalCells=NodeSet,
            )

        def Plan(PortFingerprint, ChannelFingerprint):
            Path = ((0, 2, 0), (1, 2, 0))
            Port = SimpleNamespace(
                Signal="Alpha",
                Direction="output",
                Attachment=Path[0],
                GlobalPath=Path,
                Capacity=1,
                ReservationFingerprint=PortFingerprint,
                Claims=Claims(*Path),
                GlobalClaims=Claims(*Path),
            )
            return SimpleNamespace(
                Ports=(Port,),
                PlanningChannels=(SimpleNamespace(
                    Signal="Alpha",
                    ReservationFingerprint=ChannelFingerprint,
                ),),
                GlobalKeepoutNodes=frozenset({
                    (0, 2, 0), (1, 2, 0), (2, 2, 0),
                }),
                ComponentGraphFingerprint="component",
                ResourceGraphFingerprint="resource",
                TechnologyFingerprint="technology",
            )

        def Identity(PlanValue):
            return BuildPhysicalSignalApertureCandidateDomainIdentity(
                BuildCertifiedPhysicalComponentApertureDomain(
                    PlanValue,
                    Complete=True,
                ),
                "Alpha",
                "authoritative-request-domain",
                ((2, 2, 0),),
                CoverageCursor=3,
                Complete=False,
            )

        Base = Identity(Plan("port-a", "channel-a"))
        ChangedPort = Identity(Plan("port-b", "channel-a"))
        ChangedChannel = Identity(Plan("port-a", "channel-b"))

        self.assertEqual(
            Base.StableDomainFingerprint,
            ChangedPort.StableDomainFingerprint,
        )
        self.assertEqual(
            Base.StableDomainFingerprint,
            ChangedChannel.StableDomainFingerprint,
        )
        self.assertEqual(
            Base.ApertureFingerprint,
            ChangedPort.ApertureFingerprint,
        )
        self.assertNotEqual(
            Base.ChannelReservationFingerprint,
            ChangedChannel.ChannelReservationFingerprint,
        )
        self.assertEqual(
            Base.DomainFingerprint,
            ChangedPort.DomainFingerprint,
        )
        self.assertNotEqual(
            Base.DomainFingerprint,
            ChangedChannel.DomainFingerprint,
        )

    def testCompleteApertureDomainRejectsMissingCrossingChannel(self) -> None:
        Port = SimpleNamespace(
            Signal="Alpha",
            Direction="output",
            Attachment=(0, 2, 0),
            GlobalPath=((0, 2, 0), (1, 2, 0)),
            Capacity=1,
            ReservationFingerprint="port-a",
            Claims=RoutingResourceClaims(
                WireCells=frozenset(((0, 2, 0), (1, 2, 0))),
            ),
        )
        Plan = SimpleNamespace(
            Ports=(Port,),
            PlanningChannels=(),
            GlobalKeepoutNodes=frozenset(((0, 2, 0),)),
            ComponentGraphFingerprint="component",
            ResourceGraphFingerprint="resource",
            TechnologyFingerprint="technology",
        )

        with self.assertRaisesRegex(ValueError, "missing channels"):
            BuildCertifiedPhysicalComponentApertureDomain(
                Plan,
                Complete=True,
            )

    def testExteriorDomainReusesAcrossChangedSiblingGlobalPlan(self) -> None:
        def Claims(*Nodes):
            NodeSet = frozenset(Nodes)
            return RoutingResourceClaims(
                WireCells=NodeSet,
                ElectricalCells=NodeSet,
            )

        def Plan(BetaPath):
            AlphaPath = ((0, 2, 0), (1, 2, 0))
            Ports = (
                SimpleNamespace(
                    Signal="Alpha",
                    Direction="output",
                    Attachment=AlphaPath[0],
                    GlobalPath=AlphaPath,
                    Capacity=1,
                    ReservationFingerprint="port-alpha",
                    Claims=Claims(*AlphaPath),
                    GlobalClaims=Claims(*AlphaPath),
                ),
                SimpleNamespace(
                    Signal="Beta",
                    Direction="output",
                    Attachment=BetaPath[0],
                    GlobalPath=tuple(BetaPath),
                    Capacity=1,
                    ReservationFingerprint="port-beta:" + str(BetaPath),
                    Claims=Claims(*BetaPath),
                    GlobalClaims=Claims(*BetaPath),
                ),
            )
            return SimpleNamespace(
                Ports=Ports,
                PlanningChannels=tuple(
                    SimpleNamespace(
                        Signal=Value.Signal,
                        ReservationFingerprint="channel:" + Value.Signal,
                    )
                    for Value in Ports
                ),
                GlobalKeepoutNodes=frozenset({
                    (0, 2, 0), (1, 2, 0), (2, 2, 0), (3, 2, 0),
                }),
                ComponentGraphFingerprint="component",
                ResourceGraphFingerprint="resource",
                TechnologyFingerprint="technology",
            )

        def Identity(PlanValue):
            return BuildPhysicalSignalApertureCandidateDomainIdentity(
                BuildCertifiedPhysicalComponentApertureDomain(
                    PlanValue,
                    Complete=True,
                ),
                "Alpha",
                "exterior-region-and-request-domain",
                ((2, 2, 0), (3, 2, 0)),
                CoverageCursor=0,
                Complete=False,
            )

        FirstPlan = Plan(((4, 2, 0), (5, 2, 0)))
        SecondPlan = Plan(((4, 2, 1), (5, 2, 1)))
        FirstIdentity = Identity(FirstPlan)
        SecondIdentity = Identity(SecondPlan)
        OldSiblingOnly = SimpleNamespace(
            CandidateId="old-sibling-only",
            Claims=Claims((4, 2, 0)),
        )
        NewSiblingOnly = SimpleNamespace(
            CandidateId="new-sibling-only",
            Claims=Claims((4, 2, 1)),
        )
        Clear = SimpleNamespace(
            CandidateId="clear",
            Claims=Claims((8, 2, 0)),
        )
        Cache = {
            FirstIdentity.StableDomainFingerprint: (
                MergePhysicalSignalRouteDomainDescriptorProgress(
                    None,
                    PreSiblingDomainFingerprint=(
                        FirstIdentity.StableDomainFingerprint
                    ),
                    Signal="Alpha",
                    RequestDomainFingerprint="request-domain",
                    RequestDescriptorFingerprints=("shape-0",),
                    CompletedDescriptorFingerprints=("shape-0",),
                    Candidates=(),
                    CandidateMetadata={},
                )
            )
        }
        RetainPhysicalSignalRouteDomainDescriptorProgress(
            Cache,
            PreSiblingDomainFingerprint=(
                FirstIdentity.StableDomainFingerprint
            ),
            Signal="Alpha",
            RequestDomainFingerprint="request-domain",
            RequestDescriptorFingerprints=("shape-0",),
            CompletedDescriptorFingerprints=("shape-0",),
            Candidates=(OldSiblingOnly, NewSiblingOnly, Clear),
            CandidateMetadata={
                "old-sibling-only": ("X", 0, 0, 0),
                "new-sibling-only": ("X", 1, 0, 0),
                "clear": ("X", 2, 0, 0),
            },
        )
        RetainCompletePhysicalSignalRouteDomainContinuations(
            Cache,
            {"Alpha": FirstIdentity},
            {"Alpha": ("shape-0",)},
            {"Alpha": "request-domain"},
            {"Alpha": 0},
            {"Alpha": (OldSiblingOnly, NewSiblingOnly, Clear)},
            {"Alpha": {
                "old-sibling-only": ("X", 0, 0, 0),
                "new-sibling-only": ("X", 1, 0, 0),
                "clear": ("X", 2, 0, 0),
            }},
        )

        Restored = SelectReplayablePhysicalSignalRouteDomainContinuation(
            Cache,
            SecondIdentity.StableDomainFingerprint,
            "Alpha",
            "request-domain",
            ("shape-0",),
        )

        self.assertIsNotNone(Restored)
        assert Restored is not None
        self.assertEqual(
            FirstIdentity.StableDomainFingerprint,
            SecondIdentity.StableDomainFingerprint,
        )
        CurrentBeta = next(
            Port for Port in SecondPlan.Ports if Port.Signal == "Beta"
        )
        Filtered = FilterPhysicalCandidatesAgainstSiblingApertures(
            Restored.Candidates,
            (("Beta", CurrentBeta.GlobalClaims),),
        )
        self.assertEqual(
            tuple(Value.CandidateId for Value in Filtered),
            ("clear", "old-sibling-only"),
        )

    def testSiblingApertureDiagnosticsSeparateLocalInteriorOwnership(
        self,
    ) -> None:
        def Claims(*Nodes):
            Values = frozenset(Nodes)
            return RoutingResourceClaims(
                WireCells=Values,
                ElectricalCells=Values,
            )

        CandidateClaims = Claims((0, 0, 0), (10, 0, 0))
        FullSiblingClaims = (
            ("LocalOnly", Claims((0, 0, 0), (1, 0, 0))),
            ("Global", Claims((10, 0, 0), (11, 0, 0))),
        )
        GlobalPathSiblingClaims = (
            ("LocalOnly", Claims((2, 0, 0))),
            ("Global", Claims((10, 0, 0), (11, 0, 0))),
        )

        Full, Global, LocalInteriorOnly = (
            ClassifySiblingApertureSeamOwnershipConflicts(
                CandidateClaims,
                FullSiblingClaims,
                GlobalPathSiblingClaims,
            )
        )

        self.assertEqual(Full, ("Global", "LocalOnly"))
        self.assertEqual(Global, ("Global",))
        self.assertEqual(LocalInteriorOnly, ("LocalOnly",))

    def testCompletePreSiblingPairNoSupportIgnoresThirdApertures(
        self,
    ) -> None:
        def Candidate(Signal, CandidateId, Node):
            return SimpleNamespace(
                Signal=Signal,
                CandidateId=CandidateId,
                Claims=RoutingResourceClaims(
                    WireCells=frozenset((Node,)),
                    ElectricalCells=frozenset((Node,)),
                ),
            )

        First = (Candidate("Alpha", "alpha", (1, 2, 3)),)
        Conflicting = (Candidate("Beta", "beta", (1, 2, 3)),)
        Supported = (Candidate("Beta", "beta-clear", (9, 2, 3)),)

        self.assertTrue(
            CompletePhysicalCandidatePairDomainsHaveNoSupport(
                First,
                Conflicting,
            )
        )
        self.assertFalse(
            CompletePhysicalCandidatePairDomainsHaveNoSupport(
                First,
                (*Conflicting, *Supported),
            )
        )

    def testRequestApertureNoGoodMinimizesRedundantSibling(self) -> None:
        NoGood = BuildMinimalPhysicalRequestApertureNoGood(
            "Alpha",
            "request-alpha",
            (("Beta", "Gamma"), ("Beta",), ("Beta", "Gamma")),
            {
                "Beta": "aperture-beta",
                "Gamma": "aperture-gamma",
            },
        )

        self.assertEqual(NoGood, frozenset((
            ("Alpha", "request-factor:request-alpha"),
            ("Beta", "aperture-factor:aperture-beta"),
        )))

    def testCompleteRequestCertifiesAllUnsupportedAlternativeApertures(
        self,
    ) -> None:
        def Claims(*Nodes):
            Values = frozenset(Nodes)
            return RoutingResourceClaims(
                WireCells=Values,
                ElectricalCells=Values,
            )

        Candidates = (
            SimpleNamespace(Claims=Claims((1, 2, 3))),
            SimpleNamespace(Claims=Claims((2, 2, 3))),
        )
        BoundaryDomains = {
            "Blocker": (
                SimpleNamespace(
                    ApertureContractFingerprint="blocks-both",
                    GlobalClaims=Claims((1, 2, 3), (2, 2, 3)),
                ),
                SimpleNamespace(
                    ApertureContractFingerprint="blocks-one",
                    GlobalClaims=Claims((1, 2, 3)),
                ),
            ),
            "Clear": (
                SimpleNamespace(
                    ApertureContractFingerprint="clear",
                    GlobalClaims=Claims((9, 2, 3)),
                ),
            ),
        }

        Clauses = BuildCompletePhysicalRequestAlternativeApertureNoGoods(
            "Victim",
            "global-victim",
            Candidates,
            BoundaryDomains,
        )

        self.assertEqual(Clauses, (
            frozenset((
                ("Victim", "global-victim"),
                ("Blocker", "blocks-both"),
            )),
        ))

    def testPhysicalOrdinaryPortalReusePinsOnlySameKeepoutPaths(
        self,
    ) -> None:
        Placed = object()
        Resources = RoutingResources(
            RoutingStaticGeometry(frozenset(), frozenset())
        )
        Cache = replace(
            self.BuildRawPortalCache(
                Placed,
                Resources,
                {"Ordinary": 2, "PhysicalPort": 2},
            ),
            PhysicalGlobalKeepoutFingerprint="keepout-a",
        )
        ReusePlan = RawPortalGeometryReusePlan(
            Cache=Cache,
            ReusedSignals=frozenset((
                "Ordinary",
                "PhysicalPort",
            )),
            GeneratedSignals=frozenset(),
            ExactMatch=False,
            PortableAcrossPlacement=True,
            SignalPlanarTransforms=(
                ("Ordinary", "Identity", (5, 0, 7)),
                ("PhysicalPort", "Identity", (5, 0, 7)),
            ),
        )
        PhysicalTerminal = next(
            Key[1]
            for Key, _Values in Cache.PortalEntries
            if Key[0] == "PhysicalPort"
        )

        Columns = BuildPinnedOrdinaryPortalReuseColumns(
            ReusePlan,
            frozenset(((
                "PhysicalPort",
                (
                    PhysicalTerminal[0] + 5,
                    PhysicalTerminal[1],
                    PhysicalTerminal[2] + 7,
                ),
            ),)),
        )
        OrdinaryTerminal = next(
            Key[1]
            for Key, _Values in Cache.PortalEntries
            if Key[0] == "Ordinary"
        )
        self.assertIn(
            (OrdinaryTerminal[0] + 5, OrdinaryTerminal[2] + 7),
            Columns,
        )
        self.assertNotIn(
            (PhysicalTerminal[0] + 5, PhysicalTerminal[2] + 7),
            Columns,
        )

        self.assertIsNone(SelectRawPortalGeometryReusePlan(
            (Cache,),
            Placed=Placed,
            Resources=Resources,
            LayerCount=2,
            PortalLimit=6,
            PortalVariantCounts={"Ordinary": 2, "PhysicalPort": 2},
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            AccessGeometryFingerprint=("access",),
            CoordinatedSignals=frozenset(),
            PhysicalGlobalKeepoutFingerprint="keepout-b",
        ))

    def testRawPortalPartialComponentDomainExpandsAtGlobalHandoff(
        self,
    ) -> None:
        Placed = object()
        Resources = RoutingResources(
            RoutingStaticGeometry(frozenset(), frozenset())
        )
        AlphaGeometry = ("Alpha", (0, 1, 0), (), ())
        BetaGeometry = ("Beta", (3, 1, 0), (), ())
        Cache = self.BuildRawPortalCache(
            Placed,
            Resources,
            {"Alpha": 2},
            AccessGeometryFingerprint=(
                AlphaGeometry,
                BetaGeometry,
            ),
        )

        Plan = SelectRawPortalGeometryReusePlan(
            (Cache,),
            Placed=Placed,
            Resources=Resources,
            LayerCount=2,
            PortalLimit=6,
            PortalVariantCounts={"Alpha": 2, "Beta": 2},
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            AccessGeometryFingerprint=(
                AlphaGeometry,
                BetaGeometry,
            ),
            CoordinatedSignals=frozenset(),
        )

        self.assertIsNotNone(Plan)
        self.assertFalse(Plan.ExactMatch)
        self.assertEqual(Plan.ReusedSignals, frozenset({"Alpha"}))

    def testRawPortalSamePlacementReusesUnchangedSignalGeometry(
        self,
    ) -> None:
        Placed = object()
        Resources = RoutingResources(
            RoutingStaticGeometry(frozenset(), frozenset())
        )
        OldAlpha = ("Alpha", (0, 1, 0), (), ())
        NewAlpha = (
            "Alpha",
            (0, 1, 0),
            ((0, 1, 0), (1, 1, 0)),
            (),
        )
        Beta = ("Beta", (3, 1, 0), (), ())
        Cache = self.BuildRawPortalCache(
            Placed,
            Resources,
            {"Alpha": 2, "Beta": 2},
            AccessGeometryFingerprint=(OldAlpha, Beta),
        )

        Plan = SelectRawPortalGeometryReusePlan(
            (Cache,),
            Placed=Placed,
            Resources=Resources,
            LayerCount=2,
            PortalLimit=6,
            PortalVariantCounts={"Alpha": 2, "Beta": 2},
            GuideExpansion=3,
            StrictMaximumExpansions=100,
            AccessGeometryFingerprint=(NewAlpha, Beta),
            CoordinatedSignals=frozenset(),
        )

        self.assertIsNotNone(Plan)
        self.assertEqual(Plan.ReusedSignals, frozenset(("Beta",)))
        self.assertEqual(Plan.GeneratedSignals, frozenset(("Alpha",)))
        self.assertTrue(Plan.PortableAcrossPlacement)

    def testExactPortalCompletenessSchedulesOnlyMissingSignals(
        self,
    ) -> None:
        AlphaKeys = frozenset((
            ("Alpha", (0, 2, 0), 0),
            ("Alpha", (0, 2, 0), 1),
        ))
        BetaKeys = frozenset((
            ("Beta", (4, 2, 0), 0),
            ("Beta", (4, 2, 0), 1),
        ))
        Missing, ReusedSignals, GeneratedSignals = (
            PartitionExpectedGenericPortalDomainKeys(
                (*AlphaKeys, *BetaKeys),
                (*AlphaKeys, next(iter(BetaKeys))),
            )
        )

        self.assertEqual(len(Missing), 1)
        self.assertEqual({Key[0] for Key in Missing}, {"Beta"})
        self.assertEqual(ReusedSignals, frozenset(("Alpha",)))
        self.assertEqual(GeneratedSignals, frozenset(("Beta",)))

    def testOwnedTerminalPortalPartitionPreservesExactDeferredDomain(
        self,
    ) -> None:
        AlphaTerminal = (0, 2, 0)
        BetaTerminal = (4, 2, 0)
        Requests = [("alpha-0",), ("beta-0",), ("alpha-1",)]
        Metadata = [
            ("Alpha", AlphaTerminal, 0),
            ("Beta", BetaTerminal, 0),
            ("Alpha", AlphaTerminal, 1),
        ]

        OwnedRequests, OwnedMetadata, DeferredRequests, DeferredMetadata = (
            PartitionPhysicalOwnedTerminalPortalRequests(
                Requests,
                Metadata,
                frozenset((("Alpha", AlphaTerminal),)),
            )
        )

        self.assertEqual(OwnedRequests, [("alpha-0",), ("alpha-1",)])
        self.assertEqual(OwnedMetadata, [Metadata[0], Metadata[2]])
        self.assertEqual(DeferredRequests, [("beta-0",)])
        self.assertEqual(DeferredMetadata, [Metadata[1]])
        self.assertEqual(
            [*OwnedMetadata, *DeferredMetadata],
            [Metadata[0], Metadata[2], Metadata[1]],
        )
        with self.assertRaises(ValueError):
            PartitionPhysicalOwnedTerminalPortalRequests(
                Requests,
                Metadata[:-1],
                frozenset(),
            )

    def testSignalScopedPortalMergeEqualsCompleteRegeneration(
        self,
    ) -> None:
        AlphaTerminal = (0, 1, 0)
        BetaTerminal = (1, 1, 0)
        CachedAlpha = self.BuildPortal(
            "Alpha",
            AlphaTerminal,
            AlphaTerminal,
        )
        CachedBeta = self.BuildPortal(
            "Beta",
            BetaTerminal,
            BetaTerminal,
        )
        GeneratedBeta = self.BuildPortal(
            "Beta",
            BetaTerminal,
            (2, 1, 0),
        )
        CachedEntries = (
            (("Alpha", AlphaTerminal, 0), (CachedAlpha,)),
            (("Beta", BetaTerminal, 0), (CachedBeta,)),
        )
        GeneratedEntries = (
            (("Beta", BetaTerminal, 0), (GeneratedBeta,)),
        )

        Merged = MergeSignalScopedRawPortalEntries(
            CachedEntries,
            GeneratedEntries,
            frozenset({"Beta"}),
        )

        self.assertEqual(Merged, (
            (("Alpha", AlphaTerminal, 0), (CachedAlpha,)),
            (("Beta", BetaTerminal, 0), (GeneratedBeta,)),
        ))
        self.assertEqual(CachedEntries[1][1], (CachedBeta,))
        with self.assertRaises(ValueError):
            MergeSignalScopedRawPortalEntries(
                CachedEntries,
                GeneratedEntries,
                frozenset({"Alpha"}),
            )

    def testRustPortalClaimsMatchPythonPathClaims(self) -> None:
        Graph, Region, Context = self.BuildGraph()
        Values = Context.GeneratePortalCandidates(
            [(0, 1, 0)],
            [(3, 1, 0)],
            sorted(Region.Nodes),
            1,
            8,
            1_000,
        )

        self.assertEqual(len(Values), 1)
        Portal = Values[0]
        Claims = Graph.BuildRouteClaims(Portal.Path)
        self.assertEqual(set(Portal.WireClaims), set(Claims.WireCells))
        self.assertEqual(set(Portal.SupportClaims), set(Claims.SupportCells))
        self.assertEqual(set(Portal.AirClaims), set(Claims.RequiredAirCells))
        self.assertEqual(set(Portal.ElectricalClaims), set(Claims.ElectricalCells))

    def testPortalStartsRemainAnchoredToGraphAccessAndReachRoutingLayer(self) -> None:
        Graph, Region, Context = self.BuildGraph()
        AccessPath = ((-1, 1, 0), (0, 1, 0))
        Starts = SelectGraphAccessStarts(AccessPath, Region.Nodes)
        RoutingTarget = (1, 2, 0)

        self.assertEqual(Starts, ((0, 1, 0),))
        self.assertNotIn((4, 2, 0), Starts)
        self.assertEqual(
            SelectGraphAccessStarts(
                (
                    (0, 1, 0),
                    (1, 1, 0),
                    (2, 1, 0),
                ),
                frozenset({
                    (0, 1, 0),
                    (1, 1, 0),
                    (2, 1, 0),
                }),
                PreferOutermost=True,
            ),
            ((2, 1, 0),),
        )
        AccessPath = (
            (0, 1, 0),
            (0, 1, -1),
            (0, 1, -2),
        )
        self.assertTrue(
            PortalPathRespectsOutwardAccess(
                ((0, 1, 0), (0, 2, -1)),
                AccessPath,
            )
        )
        self.assertFalse(
            PortalPathRespectsOutwardAccess(
                ((0, 1, 0), (-1, 2, 0)),
                AccessPath,
            )
        )
        self.assertTrue(
            PortalPathRespectsOutwardAccess(
                ((0, 1, -1), (0, 2, -2)),
                AccessPath,
            )
        )

        Values = Context.GeneratePortalCandidates(
            list(Starts),
            [RoutingTarget],
            sorted(Region.Nodes),
            2,
            8,
            1_000,
        )

        self.assertEqual(len(Values), 1)
        PortalPath = tuple(Values[0].Path)
        self.assertEqual(PortalPath[0], Starts[0])
        self.assertEqual(PortalPath[-1], RoutingTarget)
        self.assertTrue(all(
            Graph.BuildPrimitive(First, Second) is not None
            for First, Second in zip(PortalPath, PortalPath[1:])
        ))

        TargetPortal = PinAccessPortal(
            PortalId="A:access-to-layer",
            Signal="A",
            Terminal=AccessPath[0],
            Layer=0,
            Path=PortalPath,
            Edges=frozenset(),
            Claims=RoutingResourceClaims(),
            Length=len(PortalPath),
            BendCount=0,
            ViaCount=1,
            Cost=len(PortalPath),
        )
        TargetChain = (
            *_BuildTargetPortalBranches((TargetPortal,))[0],
            *reversed(AccessPath[:-1]),
        )
        self.assertTrue(all(
            Graph.BuildPrimitive(First, Second) is not None
            for First, Second in zip(TargetChain, TargetChain[1:])
        ))

    def testBatchedPortalGenerationPreservesRequestOrder(self) -> None:
        _Graph, Region, Context = self.BuildGraph()
        AllowedNodes = sorted(Region.Nodes)
        Requests = [
            ([(0, 1, 0)], [Target], AllowedNodes, 1, 8, 1_000)
            for Target in ((3, 1, 0), (2, 1, 0), (1, 1, 0))
        ]
        First = Context.GeneratePortalCandidateBatches(Requests)
        Second = Context.GeneratePortalCandidateBatches(Requests)
        self.assertEqual(
            [Batch[0].Target for Batch in First],
            [(3, 1, 0), (2, 1, 0), (1, 1, 0)],
        )
        self.assertEqual(
            [[Value.Path for Value in Batch] for Batch in First],
            [[Value.Path for Value in Batch] for Batch in Second],
        )

    def testRouteTreeTargetsSelectedPortalOuterEndpoint(self) -> None:
        _Graph, Region, Context = self.BuildGraph()
        Portal = PinAccessPortal(
            PortalId="A:target-portal",
            Signal="A",
            Terminal=(1, 1, 0),
            Layer=0,
            Path=((3, 1, 0), (4, 1, 0)),
            Edges=frozenset(),
            Claims=RoutingResourceClaims(),
            Length=2,
            BendCount=0,
            ViaCount=0,
            Cost=2,
        )
        Branches = _BuildTargetPortalBranches((Portal,))
        RawTargetAccessBranches = [[(1, 1, 0), (2, 1, 0)]]
        CompleteBranches = _BuildTargetPortalBranches(
            (Portal,),
            (((1, 1, 0), (2, 1, 0)),),
        )

        self.assertEqual(Branches, [[(4, 1, 0), (3, 1, 0)]])
        self.assertEqual(
            CompleteBranches,
            [[(4, 1, 0), (3, 1, 0), (2, 1, 0), (1, 1, 0)]],
        )
        self.assertEqual(Branches[0][0], Portal.Path[-1])
        self.assertNotEqual(Branches, RawTargetAccessBranches)

        Tree = Context.GenerateRouteTree(
            [(0, 1, 0)],
            Branches,
            sorted(Region.Nodes),
            [(Index, 0) for Index in range(5)],
            1,
            0,
            0,
            0,
            1_000,
        )
        RawTree = Context.GenerateRouteTree(
            [(0, 1, 0)],
            RawTargetAccessBranches,
            sorted(Region.Nodes),
            [(Index, 0) for Index in range(5)],
            1,
            0,
            0,
            0,
            1_000,
        )
        self.assertIn(Portal.Path[-1], Tree)
        self.assertNotIn(Portal.Path[-1], RawTree)

    def testTargetPortalBranchKeepsSharedAccessForkAsOneChain(self) -> None:
        """A shared access prefix must not turn into a terminal-side jump."""
        Graph, Region, Context = self.BuildGraph()
        Terminal = (0, 1, 0)
        SharedAccessNode = (1, 1, 0)
        AlternateAccessLanding = (2, 1, 0)
        Portal = PinAccessPortal(
            PortalId="A:shared-access-fork",
            Signal="A",
            Terminal=Terminal,
            Layer=0,
            Path=(Terminal, SharedAccessNode),
            Edges=frozenset(),
            Claims=RoutingResourceClaims(),
            Length=2,
            BendCount=0,
            ViaCount=0,
            Cost=2,
        )

        Branch = _BuildTargetPortalBranches(
            (Portal,),
            ((Terminal, SharedAccessNode, AlternateAccessLanding),),
        )[0]

        # The old de-duplication emitted
        # ``shared -> terminal -> alternate``.  The last transition is not a
        # routing edge, so native correctly rejected the whole request.
        self.assertEqual(Branch, [SharedAccessNode, Terminal])
        self.assertTrue(all(
            Graph.BuildPrimitive(First, Second) is not None
            for First, Second in zip(Branch, Branch[1:])
        ))
        Tree = Context.GenerateRouteTree(
            [(4, 1, 0)],
            [Branch],
            sorted(Region.Nodes),
            [(Index, 0) for Index in range(5)],
            1,
            0,
            0,
            0,
            1_000,
        )
        self.assertIn(SharedAccessNode, Tree)
        self.assertIn(Terminal, Tree)

    def testPlacementAccessFabricPublishesEveryStubPortalDeterministically(
        self,
    ) -> None:
        """An unfrozen fabric owns the complete terminal escape domain."""
        Graph, _Region, _Context = self.BuildGraph()
        Terminal = (0, 1, 0)

        def BuildStub(
            Ingress: tuple[int, int, int],
            Path: tuple[tuple[int, int, int], ...],
        ) -> PlacementAccessEscapeStub:
            return PlacementAccessEscapeStub(
                Terminal=Terminal,
                Ingress=Ingress,
                Path=Path,
                PhysicalClaims=Graph.BuildRouteClaims(Path),
                CapacityResourceIds=(),
                Complete=True,
            )

        FirstStub = BuildStub(
            (1, 1, 0),
            (Terminal, (1, 1, 0)),
        )
        SecondStub = BuildStub(
            (0, 1, 1),
            (Terminal, (0, 1, 1)),
        )
        ThirdStub = BuildStub(
            (0, 3, 1),
            (Terminal, (0, 2, 1), (0, 3, 1)),
        )
        Fabric = PlacementAccessFabric(
            FabricFingerprint="fabric-domain",
            Nodes=(
                FirstStub.Ingress,
                SecondStub.Ingress,
                ThirdStub.Ingress,
            ),
            Edges=(),
            IngressNodes=(
                FirstStub.Ingress,
                SecondStub.Ingress,
                ThirdStub.Ingress,
            ),
            PhysicalClaims=RoutingResourceClaims(),
            CapacityResourceIds=(),
            TerminalDomains=(
                PlacementAccessTerminalDomain(
                    Signal="Signal",
                    Terminal=Terminal,
                    EscapeStubs=(FirstStub, SecondStub, ThirdStub),
                    Complete=True,
                ),
            ),
            TopologyKind="derived-perimeter-access-v1",
            Complete=True,
        )

        def GenericPortal(
            Layer: int,
            Signal: str = "Signal",
            PortalTerminal: tuple[int, int, int] = Terminal,
        ) -> PinAccessPortal:
            Path = ((9, 1 + 2 * Layer, 0),)
            return PinAccessPortal(
                PortalId=f"generic:{Signal}:{Layer}",
                Signal=Signal,
                Terminal=PortalTerminal,
                Layer=Layer,
                Path=Path,
                Edges=frozenset(),
                Claims=Graph.BuildRouteClaims(Path),
                Length=len(Path),
                BendCount=0,
                ViaCount=0,
                Cost=len(Path),
            )

        UnrelatedKey = ("Other", (4, 1, 0), 0)
        Unrelated = (GenericPortal(
            0,
            Signal="Other",
            PortalTerminal=UnrelatedKey[1],
        ),)
        GenericPortals = {
            ("Signal", Terminal, 0): (GenericPortal(0),),
            ("Signal", Terminal, 1): (GenericPortal(1),),
            UnrelatedKey: Unrelated,
        }

        First = ApplyPlacementAccessFabricPortalDomains(
            GenericPortals,
            Fabric,
            Graph,
            DefaultRedstoneRoutingTechnology,
            0,
            2,
        )
        Second = ApplyPlacementAccessFabricPortalDomains(
            GenericPortals,
            Fabric,
            Graph,
            DefaultRedstoneRoutingTechnology,
            0,
            2,
        )

        self.assertEqual(First, Second)
        self.assertIs(First[UnrelatedKey], Unrelated)
        self.assertEqual(
            [Portal.PortalId for Portal in First[("Signal", Terminal, 0)]],
            [
                "Signal:(0, 1, 0):0:AccessFabricDomain:fabric-domain:0",
                "Signal:(0, 1, 0):0:AccessFabricDomain:fabric-domain:1",
            ],
        )
        self.assertEqual(
            [Portal.PortalId for Portal in First[("Signal", Terminal, 1)]],
            [
                "Signal:(0, 1, 0):1:AccessFabricDomain:fabric-domain:2",
            ],
        )
        self.assertEqual(
            [Portal.Layer for Values in First.values() for Portal in Values
             if Portal.Signal == "Signal"],
            [0, 0, 1],
        )
        self.assertTrue(all(
            "generic:" not in Portal.PortalId
            for Values in First.values()
            for Portal in Values
            if Portal.Signal == "Signal"
        ))
