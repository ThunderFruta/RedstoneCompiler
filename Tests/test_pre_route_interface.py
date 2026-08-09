"""Contracts for bounded pre-route template/interface selection."""

from types import SimpleNamespace

import pytest

from Compiler.Placement.PcbFlow import (
    SelectDiverseDerivedPerimeterInterfaceTemplates,
)

from Compiler.Placement.PreRouteInterface import (
    BuildDerivedPerimeterInterfaceTemplateDomain,
    DerivedPlacementCandidate,
    DerivedPerimeterSlotDomain,
    DerivedPerimeterTerminalSlot,
    DeriveRoutingEnvelopes,
    PlacementAccessDemand,
    PreRouteInterfaceProblem,
    PreRouteInterfaceTemplate,
    PreRouteInterfaceWitness,
    SolvePreRouteInterfaceProblem,
)


def test_unused_geometry_slots_are_filled_by_face_diversity_not_objective():
    Templates = (
        SimpleNamespace(
            TemplateId="a",
            FaceSignature=(0, 4, 0, 0),
            Objective=(999,),
        ),
        SimpleNamespace(
            TemplateId="b",
            FaceSignature=(0, 0, 4, 0),
            Objective=(999,),
        ),
        SimpleNamespace(
            TemplateId="cheap",
            FaceSignature=(3, 1, 0, 0),
            Objective=(0,),
        ),
    )

    Selected = SelectDiverseDerivedPerimeterInterfaceTemplates(
        Templates,
        ((4, 0, 0, 0),),
        2,
    )

    assert tuple(Value.TemplateId for Value in Selected) == ("a", "b")


def _Slot(
    SlotId: str,
    TerminalName: str,
    Face: str,
    Bounds: tuple[int, int, int, int],
) -> DerivedPerimeterTerminalSlot:
    Directions = {
        "north": (0, 0, -1),
        "south": (0, 0, 1),
        "west": (-1, 0, 0),
        "east": (1, 0, 0),
    }
    Pin = (
        Bounds[0] if Face == "west" else Bounds[2] if Face == "east" else 0,
        1,
        Bounds[1] if Face == "north" else Bounds[3] if Face == "south" else 0,
    )
    return DerivedPerimeterTerminalSlot(
        SlotId=SlotId,
        TerminalName=TerminalName,
        Signal=TerminalName,
        Face=Face,
        Origin=(Bounds[0], 1, Bounds[1]),
        Rotation=0,
        MirrorX=False,
        MacroBounds=Bounds,
        ConnectionPin=Pin,
        ConnectionDirection=Directions[Face],
        InteriorSpan=0,
    )


def test_interface_template_domain_is_fixed_and_grouped_by_face_shape():
    Domain = DerivedPerimeterSlotDomain(
        CoreBounds=(0, 0, 4, 4),
        TerminalSlots=(
            ("A", (
                _Slot("A-north", "A", "north", (0, -2, 0, -1)),
                _Slot("A-south", "A", "south", (0, 5, 0, 6)),
            )),
            ("B", (
                _Slot("B-north", "B", "north", (2, -2, 2, -1)),
                _Slot("B-east", "B", "east", (5, 1, 6, 1)),
            )),
        ),
    )

    First = BuildDerivedPerimeterInterfaceTemplateDomain(Domain, 64)
    Second = BuildDerivedPerimeterInterfaceTemplateDomain(Domain, 64)

    assert First.Complete is True
    assert First.DomainFingerprint == Second.DomainFingerprint
    assert {Value.FaceSignature for Value in First.Templates} == {
        (2, 0, 0, 0),
        (1, 1, 0, 0),
        (1, 0, 0, 1),
        (0, 1, 0, 1),
    }
    assert len(First.Templates) == len({
        Value.FaceSignature for Value in First.Templates
    })
    assert all(Value.SlotAssignment.Success for Value in First.Templates)
    assert First.ToDictionary()["NonExhaustive"] is True


def test_interface_template_domain_work_cap_is_incomplete():
    Domain = DerivedPerimeterSlotDomain(
        CoreBounds=(0, 0, 2, 2),
        TerminalSlots=(("A", (
            _Slot("A-north", "A", "north", (0, -2, 0, -1)),
            _Slot("A-south", "A", "south", (0, 3, 0, 4)),
        )),),
    )

    Result = BuildDerivedPerimeterInterfaceTemplateDomain(Domain, 1)

    assert Result.Complete is False
    assert Result.IncompleteReason == "work-cap"


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
