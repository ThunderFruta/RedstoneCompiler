package dev.redstonecompiler.harness;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class TraceQuiescenceTrackerTest {
    @Test
    void SettlesOnlyOnTheTwentiethConsecutiveUnchangedTick() {
        TraceQuiescenceTracker Tracker = new TraceQuiescenceTracker(
                20, 200, 100, "initial");

        for (long GameTime = 101; GameTime < 120; GameTime++) {
            assertFalse(Tracker.Observe(GameTime, "initial").Settled());
        }

        TraceQuiescenceTracker.TraceQuiescenceStatus Status =
                Tracker.Observe(120, "initial");
        assertTrue(Status.Settled());
        assertFalse(Status.TimedOut());
        assertEquals(20, Status.ObservedUnchangedTicks());
        assertEquals(20, Status.ElapsedTicks());
    }

    @Test
    void InternalTraceChangeResetsAnUnchangedOutputPlateau() {
        TraceQuiescenceTracker Tracker = new TraceQuiescenceTracker(
                20, 200, 0, "output=false;internal=0");
        for (long GameTime = 1; GameTime <= 19; GameTime++) {
            Tracker.Observe(GameTime, "output=false;internal=0");
        }

        TraceQuiescenceTracker.TraceQuiescenceStatus Changed =
                Tracker.Observe(20, "output=false;internal=1");

        assertFalse(Changed.Settled());
        assertEquals(0, Changed.ObservedUnchangedTicks());
        assertEquals(20, Changed.LastObservedChangeTick());
        for (long GameTime = 21; GameTime < 40; GameTime++) {
            assertFalse(Tracker.Observe(
                    GameTime, "output=false;internal=1").Settled());
        }
        assertTrue(Tracker.Observe(
                40, "output=false;internal=1").Settled());
    }

    @Test
    void SkippedGameTickResetsTheProofWindow() {
        TraceQuiescenceTracker Tracker = new TraceQuiescenceTracker(
                20, 200, 0, "steady");
        for (long GameTime = 1; GameTime <= 10; GameTime++) {
            Tracker.Observe(GameTime, "steady");
        }

        TraceQuiescenceTracker.TraceQuiescenceStatus Gap =
                Tracker.Observe(12, "steady");

        assertEquals(0, Gap.ObservedUnchangedTicks());
        assertEquals(1, Gap.UnobservedTickGapCount());
        for (long GameTime = 13; GameTime < 32; GameTime++) {
            assertFalse(Tracker.Observe(GameTime, "steady").Settled());
        }
        assertTrue(Tracker.Observe(32, "steady").Settled());
    }

    @Test
    void DuplicateObservationDoesNotAdvanceTheProofWindow() {
        TraceQuiescenceTracker Tracker = new TraceQuiescenceTracker(
                20, 200, 50, "steady");

        TraceQuiescenceTracker.TraceQuiescenceStatus Duplicate =
                Tracker.Observe(50, "changed-within-same-tick");

        assertEquals(0, Duplicate.ElapsedTicks());
        assertEquals(0, Duplicate.ObservedUnchangedTicks());
        assertEquals(0, Duplicate.UnobservedTickGapCount());
        assertEquals(1, Tracker.Observe(51, "steady").ObservedUnchangedTicks());
    }

    @Test
    void ReportsTimeoutOnlyWhenTheDeadlineArrivesWithoutSettlement() {
        TraceQuiescenceTracker Tracker = new TraceQuiescenceTracker(
                20, 200, 0, "state-0");
        TraceQuiescenceTracker.TraceQuiescenceStatus Status = Tracker.Status();
        for (long GameTime = 1; GameTime <= 200; GameTime++) {
            Status = Tracker.Observe(GameTime, "state-" + GameTime);
        }

        assertFalse(Status.Settled());
        assertTrue(Status.TimedOut());
        assertEquals(200, Status.ElapsedTicks());
        assertEquals(200, Status.LastObservedChangeTick());
    }

    @Test
    void SettlementWinsWhenTheQuietWindowCompletesAtTheDeadline() {
        TraceQuiescenceTracker Tracker = new TraceQuiescenceTracker(
                20, 20, 80, "steady");
        TraceQuiescenceTracker.TraceQuiescenceStatus Status = Tracker.Status();
        for (long GameTime = 81; GameTime <= 100; GameTime++) {
            Status = Tracker.Observe(GameTime, "steady");
        }

        assertTrue(Status.Settled());
        assertFalse(Status.TimedOut());
    }

    @Test
    void RejectsInvalidPolicyAndBackwardTime() {
        assertThrows(IllegalArgumentException.class, () ->
                new TraceQuiescenceTracker(0, 200, 0, "steady"));
        assertThrows(IllegalArgumentException.class, () ->
                new TraceQuiescenceTracker(20, 19, 0, "steady"));
        TraceQuiescenceTracker Tracker = new TraceQuiescenceTracker(
                20, 200, 10, "steady");
        assertThrows(IllegalArgumentException.class, () ->
                Tracker.Observe(9, "steady"));
    }
}
