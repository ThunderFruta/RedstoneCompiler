from time import monotonic

from PhysicalDesign.Routing.Workers.EligibilityPreparation import ImmutableEligibilityPreparationRequest, ImmutableEligibilityPreparationOutcome, MergeImmutableEligibilityPreparationOutcomes, PrepareImmutableEligibilityWorker


def _Outcome(
    QueueOrder: int,
    *,
    Epoch: int = 4,
    Variant: int = 0,
) -> ImmutableEligibilityPreparationOutcome:
    return ImmutableEligibilityPreparationOutcome(
        ComponentVariant=Variant,
        QueueOrder=QueueOrder,
        PlacementFingerprint=f"placement-{QueueOrder}",
        CutEpoch=Epoch,
        Status="prepared",
    )


def test_immutable_eligibility_merge_is_queue_ordered_after_out_of_order_finish():
    Merged, Discarded = MergeImmutableEligibilityPreparationOutcomes(
        (_Outcome(5), _Outcome(1), _Outcome(3), _Outcome(2)),
        CurrentCutEpoch=4,
        DeadlineAt=monotonic() + 5.0,
    )

    assert [Outcome.QueueOrder for Outcome in Merged] == [1, 2, 3, 5]
    assert Discarded == 0


def test_immutable_eligibility_merge_discards_stale_epochs_without_unsat():
    Merged, Discarded = MergeImmutableEligibilityPreparationOutcomes(
        (_Outcome(0, Epoch=3), _Outcome(1, Epoch=4)),
        CurrentCutEpoch=4,
        DeadlineAt=monotonic() + 5.0,
    )

    assert [Outcome.QueueOrder for Outcome in Merged] == [1]
    assert Discarded == 1


def test_immutable_eligibility_worker_deadline_is_incomplete_not_unsat():
    Outcome = PrepareImmutableEligibilityWorker(
        ImmutableEligibilityPreparationRequest(
            ComponentVariant=0,
            QueueOrder=0,
            PlacementFingerprint="expired",
            CutEpoch=1,
            Placement=None,
            Policy=None,
            StateFingerprint="expired",
            LocalRouteFingerprint="",
            DeadlineAt=monotonic() - 0.01,
        )
    )

    assert Outcome.Status == "incomplete"
    assert Outcome.Failure is None
