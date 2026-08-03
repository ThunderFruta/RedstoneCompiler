from __future__ import annotations

from dataclasses import replace
import unittest

from Compiler.Placement.PcbFlow import ComputeInterfaceStateCountBound, TopologyDemandProfile
from Compiler.Routing.Policy import (
    BuildRoutingPolicyForCircuit,
    LocalFirstPhysicalDesignPolicy,
    RoutingCircuitComplexityProfile,
)


class RoutingPolicyGenericProfileTests(unittest.TestCase):
    def test_build_routing_policy_for_circuit_is_metric_only(self):
        Profile = RoutingCircuitComplexityProfile(
            SignalCount=60,
            GateCount=72,
            RoutingGraphEdgeCount=120,
            MaximumFanout=6,
            ReconvergentFanoutCount=3,
            PeakBoundaryDemand=30,
            MandatoryAccessConflictResources=1,
        )
        First = BuildRoutingPolicyForCircuit(
            LocalFirstPhysicalDesignPolicy,
            ComplexityProfile=Profile,
        )
        Second = BuildRoutingPolicyForCircuit(
            LocalFirstPhysicalDesignPolicy,
            ComplexityProfile=replace(
                Profile,
                SignalCount=Profile.SignalCount + 4,
                RoutingGraphEdgeCount=Profile.RoutingGraphEdgeCount + 1,
            ),
        )
        self.assertEqual(First, Second)

        Simpler = BuildRoutingPolicyForCircuit(
            LocalFirstPhysicalDesignPolicy,
            ComplexityProfile=replace(Profile, SignalCount=10, GateCount=8),
        )
        self.assertNotEqual(First, Simpler)

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
