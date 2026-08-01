import json
import unittest

from Compiler.Routing.Failures import (
    RoutingAssignmentCut,
    RoutingAssignmentCutClassification,
    RoutingFailure,
    RoutingFailureReason,
)


class RoutingAssignmentCutTests(unittest.TestCase):
    def testFromFailurePreservesCompleteCanonicalConflictGraph(self) -> None:
        ConflictGraph = {
            "ResourceHotspots": [[8, 1, 3], [2, 0, 4]],
            "PriorityRelocationTerminals": [[9, 1, 5], [3, 1, 5]],
            "CandidateCounts": {"Zulu": 2, "Alpha": 1},
            "PairwiseIncompatibleEdges": [
                ["Zulu", "Alpha"],
                ["Alpha", "Zulu"],
            ],
            "NoCandidateSignals": ["Zulu"],
            "ConflictSignals": ["Zulu", "Alpha"],
            "PriorityRelocationSignals": ["Zulu"],
            "RelocationSignals": ["Zulu", "Alpha"],
            "Classification": "higher-order-placement-conflict",
            "FutureStructuredEvidence": {
                "Nested": ({"Position": (4, 5, 6)},),
            },
        }
        Failure = RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="TrackAssignment",
            AffectedNets=("Additional",),
            Diagnostics={
                "ConflictGraph": ConflictGraph,
                "ConflictFingerprint": "conflict-fingerprint",
                "CandidateFingerprint": "candidate-fingerprint",
                "EffectiveWorkFingerprint": "work-fingerprint",
            },
        )

        Cut = RoutingAssignmentCut.FromFailure(
            Failure,
            SourceCandidateId="Placement-001",
            MandatoryAccessOwnershipFingerprint="ownership-fingerprint",
        )
        self.assertIsNotNone(Cut)
        assert Cut is not None
        ConflictGraph["FutureStructuredEvidence"] = {"Mutated": True}

        self.assertEqual(
            Cut.Classification,
            RoutingAssignmentCutClassification.HigherOrderPlacementConflict,
        )
        self.assertEqual(
            Cut.ConflictGraphJson,
            (
                '{"CandidateCounts":{"Alpha":1,"Zulu":2},'
                '"Classification":"higher-order-placement-conflict",'
                '"ConflictSignals":["Zulu","Alpha"],'
                '"FutureStructuredEvidence":{"Nested":'
                '[{"Position":[4,5,6]}]},'
                '"NoCandidateSignals":["Zulu"],'
                '"PairwiseIncompatibleEdges":'
                '[["Zulu","Alpha"],["Alpha","Zulu"]],'
                '"PriorityRelocationSignals":["Zulu"],'
                '"PriorityRelocationTerminals":'
                '[[9,1,5],[3,1,5]],'
                '"RelocationSignals":["Zulu","Alpha"],'
                '"ResourceHotspots":[[8,1,3],[2,0,4]]}'
            ),
        )
        self.assertEqual(
            Cut.ConflictGraph["FutureStructuredEvidence"],
            {"Nested": [{"Position": [4, 5, 6]}]},
        )
        self.assertEqual(
            Cut.RelocationSignals,
            ("Alpha", "Zulu"),
        )
        self.assertEqual(Cut.PriorityRelocationSignals, ("Zulu",))
        self.assertEqual(
            Cut.ConflictSignals,
            ("Additional", "Alpha", "Zulu"),
        )
        self.assertEqual(Cut.NoCandidateSignals, ("Zulu",))
        self.assertEqual(
            Cut.PairwiseConflictEdges,
            (("Alpha", "Zulu"),),
        )
        self.assertEqual(
            Cut.CandidateCounts,
            (("Alpha", 1), ("Zulu", 2)),
        )
        self.assertEqual(
            Cut.ResourceHotspots,
            ((2, 0, 4), (8, 1, 3)),
        )
        self.assertEqual(
            Cut.PriorityRelocationTerminals,
            ((3, 1, 5), (9, 1, 5)),
        )
        self.assertEqual(Cut.SourceCandidateId, "Placement-001")
        self.assertEqual(
            Cut.MandatoryAccessOwnershipFingerprint,
            "ownership-fingerprint",
        )
        self.assertEqual(
            Cut.CandidateFingerprint,
            "candidate-fingerprint",
        )
        self.assertEqual(
            Cut.ConflictFingerprint,
            "conflict-fingerprint",
        )
        self.assertEqual(Cut.EffectiveWorkFingerprint, "work-fingerprint")
        json.dumps(Cut.ToDictionary(), sort_keys=True)

    def testCanonicalGraphIsIndependentOfMappingAndTupleSpelling(self) -> None:
        FirstFailure = RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="TrackAssignment",
            Diagnostics={
                "ConflictGraph": {
                    "Classification": "pairwise-incompatibility",
                    "ConflictSignals": ("B", "A"),
                    "Nested": {"Second": 2, "First": 1},
                },
            },
        )
        SecondFailure = RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="TrackAssignment",
            Diagnostics={
                "ConflictGraph": {
                    "Nested": {"First": 1, "Second": 2},
                    "ConflictSignals": ["B", "A"],
                    "Classification": "pairwise-incompatibility",
                },
            },
        )

        First = RoutingAssignmentCut.FromFailure(FirstFailure)
        Second = RoutingAssignmentCut.FromFailure(SecondFailure)
        self.assertIsNotNone(First)
        self.assertIsNotNone(Second)
        assert First is not None and Second is not None
        self.assertEqual(First.ConflictGraphJson, Second.ConflictGraphJson)
        self.assertEqual(
            First.ConflictFingerprint,
            Second.ConflictFingerprint,
        )

    def testFromFailureFindsLatestEscalationFingerprintsAndSignals(self) -> None:
        Failure = RoutingFailure(
            Reason=RoutingFailureReason.RuntimeBudgetExceeded,
            Stage="Candidate",
            Diagnostics={
                "ConflictGraph": {
                    "Classification": "new-future-cut-class",
                    "ConflictSignals": ["GraphSignal"],
                },
                "RoutingEscalationState": {
                    "CandidateFingerprint": "state-candidates",
                    "ConflictFingerprint": "state-conflicts",
                },
                "EscalationHistory": (
                    {
                        "RelocationSignals": ["Old"],
                        "EffectiveWorkFingerprint": "old-work",
                    },
                    {
                        "RelocationSignals": ["Latest"],
                        "PriorityRelocationSignals": ["Priority"],
                        "EffectiveWorkFingerprint": "latest-work",
                        "CandidateId": "Placement-009",
                        "MandatoryAccessOwnershipFingerprint": "ownership-009",
                    },
                ),
            },
        )

        Cut = RoutingAssignmentCut.FromFailure(Failure)
        self.assertIsNotNone(Cut)
        assert Cut is not None
        self.assertIsInstance(
            Cut.Classification,
            RoutingAssignmentCutClassification,
        )
        self.assertEqual(Cut.Classification.value, "new-future-cut-class")
        self.assertEqual(Cut.RelocationSignals, ("Latest",))
        self.assertEqual(Cut.PriorityRelocationSignals, ("Priority",))
        self.assertEqual(Cut.CandidateFingerprint, "state-candidates")
        self.assertEqual(Cut.ConflictFingerprint, "state-conflicts")
        self.assertEqual(Cut.EffectiveWorkFingerprint, "latest-work")
        self.assertEqual(Cut.SourceCandidateId, "Placement-009")
        self.assertEqual(
            Cut.MandatoryAccessOwnershipFingerprint,
            "ownership-009",
        )

    def testProvisionalHistoricalPairIsNotPromotedAfterStarvation(
        self,
    ) -> None:
        Failure = RoutingFailure(
            Reason=RoutingFailureReason.TrackAssignmentConflict,
            Stage="Candidate",
            AffectedNets=("Starved",),
            Diagnostics={
                "ConflictGraph": {
                    "Classification": (
                        "candidate-starvation-placement-conflict"
                    ),
                    "ConflictSignals": ["Starved"],
                    "NoCandidateSignals": ["Starved"],
                    "RelocationSignals": ["Starved"],
                },
                "EscalationHistory": (
                    {
                        "Stage": "TrackAssignment",
                        "Action": "regenerate-affected-candidates",
                        "CandidateDomainPairExpansion": True,
                        "ExactPairEndpointExpansion": True,
                        "AffectedSignals": ["A", "B", "Starved"],
                        "PairwiseIncompatibleEdges": [["A", "B"]],
                    },
                ),
            },
        )

        Cut = RoutingAssignmentCut.FromFailure(Failure)

        self.assertIsNotNone(Cut)
        assert Cut is not None
        self.assertEqual(Cut.NoCandidateSignals, ("Starved",))
        self.assertEqual(Cut.PairwiseConflictEdges, ())

    def testTrackAssignmentFailureWithMalformedDiagnosticsStillFormsCut(
        self,
    ) -> None:
        Cut = RoutingAssignmentCut.FromFailure(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="TrackAssignment",
                AffectedNets=("OnlySignal",),
                Diagnostics={"ConflictGraph": "not-a-mapping"},
            )
        )
        self.assertIsNotNone(Cut)
        assert Cut is not None
        self.assertEqual(
            Cut.Classification,
            RoutingAssignmentCutClassification.Unclassified,
        )
        self.assertEqual(Cut.ConflictGraphJson, "{}")
        self.assertEqual(Cut.ConflictSignals, ("OnlySignal",))

    def testFromFailureReturnsNoneWithoutAssignmentCutEvidence(self) -> None:
        self.assertIsNone(
            RoutingAssignmentCut.FromFailure(
                RoutingFailure(
                    Reason=RoutingFailureReason.RuntimeBudgetExceeded,
                    Stage="DetailedRouting",
                    Diagnostics={"Deadline": {"RemainingSeconds": 0}},
                )
            )
        )

    def testCandidateFailureFingerprintIsRetainedAsCandidateFingerprint(
        self,
    ) -> None:
        Cut = RoutingAssignmentCut.FromFailure(
            RoutingFailure(
                Reason=RoutingFailureReason.TrackAssignmentConflict,
                Stage="Candidate",
                Diagnostics={
                    "ConflictGraph": {
                        "Classification": (
                            "candidate-starvation-placement-conflict"
                        ),
                        "ConflictSignals": ["Generate0"],
                        "NoCandidateSignals": ["Generate0"],
                    },
                    "CandidateFailureFingerprint": "starvation-domain",
                },
            )
        )
        self.assertIsNotNone(Cut)
        assert Cut is not None
        self.assertEqual(Cut.CandidateFingerprint, "starvation-domain")
        self.assertEqual(Cut.NoCandidateSignals, ("Generate0",))


if __name__ == "__main__":
    unittest.main()
