"""GlobalGuideStageBoundaryTests contracts."""

from ._authoritative_planner_contracts import *


class GlobalGuideStageBoundaryTests(unittest.TestCase):
    def testCompleteComponentPreparationDefersGlobalGuidePlanning(
        self,
    ) -> None:
        self.assertFalse(ShouldBuildCapacityAwareGlobalGuidePlan(
            Enabled=True,
            PrepareComponentRoutingProblemOnly=True,
            RequireCompleteClusterInterfaceDomain=True,
            HasInterClusterRoutingChannel=True,
        ))
        for ComponentOnly, Complete, HasChannel in (
            (False, True, True),
            (True, False, True),
            (True, True, False),
        ):
            self.assertTrue(ShouldBuildCapacityAwareGlobalGuidePlan(
                Enabled=True,
                PrepareComponentRoutingProblemOnly=ComponentOnly,
                RequireCompleteClusterInterfaceDomain=Complete,
                HasInterClusterRoutingChannel=HasChannel,
            ))
        self.assertFalse(ShouldBuildCapacityAwareGlobalGuidePlan(
            Enabled=False,
            PrepareComponentRoutingProblemOnly=False,
            RequireCompleteClusterInterfaceDomain=False,
            HasInterClusterRoutingChannel=False,
        ))

    def testComponentPreparationProfilesUsePhysicalInteractionEnvelope(
        self,
    ) -> None:
        def Profile(X: int, Z: int) -> SimpleNamespace:
            Terminal = (X, 1, Z)
            return SimpleNamespace(
                Root=Terminal,
                Targets=(),
                SourceAccessPath=(Terminal,),
                TargetAccessPaths={},
            )

        Profiles = {
            "Owned": Profile(10, 10),
            "NearForeign": Profile(17, 10),
            "FarForeign": Profile(40, 40),
        }
        Channel = SimpleNamespace(Lanes=(
            SimpleNamespace(Cells=((10, 7, 10), (11, 7, 10))),
        ))

        Selected = SelectComponentPreparationProfiles(
            Profiles,
            frozenset(("Owned",)),
            Channel,
            (),
            GuideExpansion=3,
            TrackPitch=3,
        )

        self.assertEqual(
            set(Selected),
            {"Owned", "NearForeign"},
        )
        Renamed = SelectComponentPreparationProfiles(
            {
                "C": Profiles["Owned"],
                "P": Profiles["NearForeign"],
                "Q": Profiles["FarForeign"],
            },
            frozenset(("C",)),
            Channel,
            (),
            GuideExpansion=3,
            TrackPitch=3,
        )
        self.assertEqual(set(Renamed), {"C", "P"})
