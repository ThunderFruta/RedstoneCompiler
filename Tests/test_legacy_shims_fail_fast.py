import unittest
from types import SimpleNamespace

from Compiler.Routing.ChannelPlanner import BuildChannelPlan
from Compiler.Routing.Actions.ConflictRepair import (
    ExpandConflictRepairNeighborhood,
    SelectConflictRepairSignals,
)
from Compiler.Routing.Actions.Repeaters import BuildRepeaters
from Compiler.Routing.ChannelPlanner import ChannelPlan
from Compiler.Routing.TrackAssignment import AssignGlobalTracks


class LegacyShimFailFastTests(unittest.TestCase):
    def testBuildChannelPlanFailsHard(self) -> None:
        with self.assertRaises(NotImplementedError):
            BuildChannelPlan(SimpleNamespace(PlacedGates=[]))

    def testAssignGlobalTracksFailsHard(self) -> None:
        Plan = ChannelPlan(
            Profiles={},
            SignalOrder=(),
            TrunkSignals=frozenset(),
            Guides={},
            CorridorUsage={},
            CorridorCosts={},
            CorridorCapacity=1,
            Layers={},
            ResourceUsage={},
            ResourceOverflow={},
            ResourceClaimsBySignal={},
            SourceAccessTransitions={},
            TargetAccessTransitions={},
        )
        with self.assertRaises(NotImplementedError):
            AssignGlobalTracks(Plan)

    def testConflictRepairHelpersFailHard(self) -> None:
        with self.assertRaises(NotImplementedError):
            SelectConflictRepairSignals({}, set(), {})
        with self.assertRaises(NotImplementedError):
            ExpandConflictRepairNeighborhood("A", set(), 1)

    def testBuildRepeatersFailsHard(self) -> None:
        with self.assertRaises(NotImplementedError):
            BuildRepeaters({}, {}, {}, {})


if __name__ == "__main__":
    unittest.main()
