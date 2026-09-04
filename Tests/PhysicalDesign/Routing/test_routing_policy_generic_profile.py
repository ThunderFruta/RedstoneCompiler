from __future__ import annotations

import unittest

from PhysicalDesign.Orchestration.Demand import ComputeInterfaceStateCountBound, TopologyDemandProfile


class RoutingPolicyGenericProfileTests(unittest.TestCase):
    def test_compute_interface_state_count_bound_is_metric_driven(self):
        Demand = TopologyDemandProfile(
            MaximumFanout=6,
            ReconvergentCutCount=2,
            QualifyingReconvergentCutCount=1,
            MaximumReconvergentFanout=5,
            PeakBoundaryDemand=30,
            MandatoryAccessConflictResources=2,
            GateFootprint=70,
        )
        First = ComputeInterfaceStateCountBound(60, Demand, 72)
        Second = ComputeInterfaceStateCountBound(60, Demand, 72)
        self.assertEqual(First, Second)
        self.assertGreaterEqual(First, 6)
        self.assertLessEqual(First, 12)
