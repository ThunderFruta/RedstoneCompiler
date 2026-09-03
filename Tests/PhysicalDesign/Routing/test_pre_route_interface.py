"""Contracts for bounded pre-route template/interface selection."""

import pytest

from PhysicalDesign.Placement.PreRouteInterface import DerivedPlacementCandidate, DeriveRoutingEnvelopes, PlacementAccessDemand, PreRouteInterfaceProblem, PreRouteInterfaceTemplate, PreRouteInterfaceWitness, SolvePreRouteInterfaceProblem


def BuildDerivedCandidate(TemplateId: str) -> DerivedPlacementCandidate:
    Demand = PlacementAccessDemand(
        ComponentCount=1,
        TerminalCount=4,
        PeakBoundaryDemand=2,
        CoreBounds=(0, 0, 5, 5),
        TrackPitch=3,
        AccessLength=3,
        MinimumRoutingLayerCount=3,
        MaximumRoutingLayerCount=3,
        TechnologyFingerprint="technology",
    )
    Envelope = DeriveRoutingEnvelopes(Demand)[0]
    return DerivedPlacementCandidate(
        CandidateId=TemplateId,
        GeometryFingerprint=f"geometry-{TemplateId}",
        Bounds=Demand.CoreBounds,
        RoutingEnvelope=Envelope,
        Complete=True,
    )


def BuildTemplate(
    ComponentId: str,
    TemplateId: str,
    Resources: tuple[str, ...],
    Objective: tuple[int, ...],
    *,
    Complete: bool = True,
) -> PreRouteInterfaceTemplate:
    Derived = BuildDerivedCandidate(TemplateId)
    return PreRouteInterfaceTemplate(
        ComponentId=ComponentId,
        TemplateId=TemplateId,
        GeometryFingerprint=f"geometry-{TemplateId}",
        LocalClaimsFingerprint=f"claims-{TemplateId}",
        TerminalDomainFingerprint=f"terminal-{TemplateId}",
        SeamDomainFingerprint=f"seam-{TemplateId}",
        DerivedPlacement=Derived,
        RoutingEnvelope=Derived.RoutingEnvelope,
        Witnesses=(PreRouteInterfaceWitness(
            WitnessId=f"witness-{TemplateId}",
            CapacityResourceIds=Resources,
            Objective=Objective,
        ),) if Complete else (),
        Complete=Complete,
        IncompleteReason="bounded-domain" if not Complete else "",
    )


def test_selects_access_separated_template_after_compact_capacity_core():
    Result = SolvePreRouteInterfaceProblem(PreRouteInterfaceProblem(
        Templates=(
            BuildTemplate("left", "compact", ("seam-0",), (1,)),
            BuildTemplate("left", "separated", ("seam-1",), (3,)),
            BuildTemplate("right", "fixed", ("seam-0",), (1,)),
        ),
    ))

    assert Result.Success is True
    assert Result.Complete is True
    assert Result.SelectedTemplateIds == (
        ("left", "separated"),
        ("right", "fixed"),
    )
    assert Result.Objective == (4,)
    assert Result.FirstConflictResourceIds == ("seam-0",)


def test_result_is_deterministic_without_component_cross_product_identity():
    Problem = PreRouteInterfaceProblem(Templates=(
        BuildTemplate("a", "compact", ("a0",), (2, 1)),
        BuildTemplate("a", "separated", ("a1",), (3, 0)),
        BuildTemplate("b", "compact", ("b0",), (1, 2)),
    ))

    First = SolvePreRouteInterfaceProblem(Problem)
    Second = SolvePreRouteInterfaceProblem(Problem)

    assert First.SelectionFingerprint == Second.SelectionFingerprint
    assert First.SelectedTemplateIds == Second.SelectedTemplateIds
    assert First.ExpansionCount == Second.ExpansionCount


def test_incomplete_domain_never_becomes_unsatisfiable():
    Result = SolvePreRouteInterfaceProblem(PreRouteInterfaceProblem(
        Templates=(BuildTemplate(
            "component", "compact", (), (), Complete=False,
        ),),
    ))

    assert Result.Success is False
    assert Result.Complete is False
    assert Result.Unsatisfiable is False
    assert Result.IncompleteReason == "incomplete-template-domain"


def test_complete_exhaustive_empty_domain_is_unsatisfiable():
    Derived = BuildDerivedCandidate("only")
    Result = SolvePreRouteInterfaceProblem(PreRouteInterfaceProblem(
        Templates=(PreRouteInterfaceTemplate(
            ComponentId="component",
            TemplateId="only",
            GeometryFingerprint="geometry",
            LocalClaimsFingerprint="claims",
            TerminalDomainFingerprint="terminal",
            SeamDomainFingerprint="seam",
            DerivedPlacement=Derived,
            RoutingEnvelope=Derived.RoutingEnvelope,
            Witnesses=(),
            Complete=True,
        ),),
        NonExhaustiveDomain=False,
    ))

    assert Result.Success is False
    assert Result.Complete is True
    assert Result.Unsatisfiable is True
    assert Result.IncompleteReason == "complete-capacity-core"


def test_work_cap_is_typed_incomplete():
    Result = SolvePreRouteInterfaceProblem(PreRouteInterfaceProblem(
        Templates=(
            BuildTemplate("a", "one", ("a",), (1,)),
            BuildTemplate("b", "one", ("b",), (1,)),
        ),
        MaximumExpansions=1,
    ))

    assert Result.Success is False
    assert Result.Complete is False
    assert Result.Unsatisfiable is False
    assert Result.IncompleteReason == "work-cap"


def test_access_demand_fingerprint_is_translation_invariant():
    First = PlacementAccessDemand(
        ComponentCount=1,
        TerminalCount=8,
        PeakBoundaryDemand=4,
        CoreBounds=(0, 0, 9, 14),
        TrackPitch=3,
        AccessLength=3,
        MinimumRoutingLayerCount=3,
        MaximumRoutingLayerCount=5,
        TechnologyFingerprint="technology",
    )
    Translated = PlacementAccessDemand(
        ComponentCount=1,
        TerminalCount=8,
        PeakBoundaryDemand=4,
        CoreBounds=(20, -7, 29, 7),
        TrackPitch=3,
        AccessLength=3,
        MinimumRoutingLayerCount=3,
        MaximumRoutingLayerCount=5,
        TechnologyFingerprint="technology",
    )

    assert First.DemandFingerprint == Translated.DemandFingerprint
    assert DeriveRoutingEnvelopes(First)[0].AccessRingTrackCount == (
        DeriveRoutingEnvelopes(Translated)[0].AccessRingTrackCount
    )


def test_measured_face_launch_demand_is_immutable_and_artifact_visible():
    MeasuredLaunches = {
        "south": 1,
        "north": 5,
    }
    Demand = PlacementAccessDemand(
        ComponentCount=1,
        TerminalCount=0,
        PeakBoundaryDemand=0,
        CoreBounds=(0, 0, 5, 5),
        TrackPitch=3,
        AccessLength=3,
        MinimumRoutingLayerCount=1,
        MaximumRoutingLayerCount=1,
        TechnologyFingerprint="measured-faces",
        ActivePerimeterFaces=("north", "south"),
        PerimeterFaceLaunchDemand=MeasuredLaunches,
    )
    MeasuredLaunches["north"] = 99

    assert Demand.PerimeterFaceLaunchDemand == (("north", 5), ("south", 1))
    assert Demand.ToDictionary()["PerimeterFaceLaunchDemand"] == {
        "north": 5,
        "south": 1,
    }
    assert Demand.DemandFingerprint != PlacementAccessDemand(
        ComponentCount=1,
        TerminalCount=0,
        PeakBoundaryDemand=0,
        CoreBounds=(0, 0, 5, 5),
        TrackPitch=3,
        AccessLength=3,
        MinimumRoutingLayerCount=1,
        MaximumRoutingLayerCount=1,
        TechnologyFingerprint="measured-faces",
        ActivePerimeterFaces=("north", "south"),
    ).DemandFingerprint


@pytest.mark.parametrize(
    "FaceLaunchDemand",
    (
        (("north", 1),),
        (("north", 1), ("south", -1)),
        (("north", 1), ("east", 0)),
        (("north", 1), ("north", 0), ("south", 0)),
    ),
)
def test_measured_face_launch_demand_requires_complete_valid_active_mapping(
    FaceLaunchDemand: tuple[tuple[str, int], ...],
):
    with pytest.raises(ValueError, match="face launch demand"):
        PlacementAccessDemand(
            ComponentCount=1,
            TerminalCount=0,
            PeakBoundaryDemand=0,
            CoreBounds=(0, 0, 5, 5),
            TrackPitch=3,
            AccessLength=3,
            MinimumRoutingLayerCount=1,
            MaximumRoutingLayerCount=1,
            TechnologyFingerprint="measured-faces",
            ActivePerimeterFaces=("north", "south"),
            PerimeterFaceLaunchDemand=FaceLaunchDemand,
        )


def test_measured_face_launch_demand_prevents_cross_face_capacity_averaging():
    Legacy = PlacementAccessDemand(
        ComponentCount=1,
        TerminalCount=0,
        PeakBoundaryDemand=8,
        CoreBounds=(0, 0, 5, 5),
        TrackPitch=3,
        AccessLength=3,
        MinimumRoutingLayerCount=2,
        MaximumRoutingLayerCount=2,
        TechnologyFingerprint="face-capacity",
    )
    Measured = PlacementAccessDemand(
        ComponentCount=1,
        TerminalCount=0,
        PeakBoundaryDemand=8,
        CoreBounds=(0, 0, 5, 5),
        TrackPitch=3,
        AccessLength=3,
        MinimumRoutingLayerCount=2,
        MaximumRoutingLayerCount=2,
        TechnologyFingerprint="face-capacity",
        PerimeterFaceLaunchDemand=(
            ("north", 8),
            ("south", 0),
            ("west", 0),
            ("east", 0),
        ),
    )

    assert DeriveRoutingEnvelopes(Legacy)[0].AccessRingTrackCount == 1
    assert DeriveRoutingEnvelopes(Measured)[0].AccessRingTrackCount == 4


def test_track_pitch_scales_derived_envelope_without_policy_values():
    Narrow = PlacementAccessDemand(
        ComponentCount=1,
        TerminalCount=12,
        PeakBoundaryDemand=4,
        CoreBounds=(0, 0, 11, 17),
        TrackPitch=2,
        AccessLength=3,
        MinimumRoutingLayerCount=3,
        MaximumRoutingLayerCount=3,
        TechnologyFingerprint="pitch-2",
    )
    Wide = PlacementAccessDemand(
        ComponentCount=1,
        TerminalCount=12,
        PeakBoundaryDemand=4,
        CoreBounds=(0, 0, 11, 17),
        TrackPitch=4,
        AccessLength=3,
        MinimumRoutingLayerCount=3,
        MaximumRoutingLayerCount=3,
        TechnologyFingerprint="pitch-4",
    )

    NarrowEnvelope = DeriveRoutingEnvelopes(Narrow)[0]
    WideEnvelope = DeriveRoutingEnvelopes(Wide)[0]
    assert NarrowEnvelope.ComponentSpacing == (
        NarrowEnvelope.AccessRingTrackCount * 2
    )
    assert WideEnvelope.ComponentSpacing == (
        WideEnvelope.AccessRingTrackCount * 4
    )
    assert NarrowEnvelope.EnvelopeFingerprint != (
        WideEnvelope.EnvelopeFingerprint
    )


def test_partial_best_before_work_cap_is_not_selected():
    Result = SolvePreRouteInterfaceProblem(PreRouteInterfaceProblem(
        Templates=(
            BuildTemplate("a", "one", ("a",), (1,)),
            BuildTemplate("a", "two", ("a2",), (2,)),
        ),
        MaximumExpansions=1,
    ))

    assert Result.Success is False
    assert Result.Complete is False
    assert Result.SelectedTemplateIds == ()
    assert Result.IncompleteReason == "work-cap"
