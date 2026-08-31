"""Focused contracts for the bounded pin-aligned graph-core portfolio."""

from __future__ import annotations

import unittest

from Compiler.Ir.Models import Gate, GateKind
from Compiler.Placement.Core.Compactness import (
    BuildPinAlignedPackedCluster,
    BuildPinAlignedPackedClusterPortfolio,
    CountPinAlignedPackedClusterPortfolio,
)


class PinAlignedPackedClusterPortfolioTests(unittest.TestCase):
    @staticmethod
    def BuildFixture() -> tuple[tuple[str, ...], dict[str, Gate]]:
        Gates = (
            Gate("N0", GateKind.NAND, ["S0"], ["A", "B"]),
            Gate("N1", GateKind.NAND, ["S1"], ["S0", "A"]),
            Gate("N2", GateKind.NAND, ["Result"], ["S1", "B"]),
        )
        InternalByName = {GateValue.Name: GateValue for GateValue in Gates}
        return tuple(InternalByName), InternalByName

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
