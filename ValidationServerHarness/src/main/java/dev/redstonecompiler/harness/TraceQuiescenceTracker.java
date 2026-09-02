package dev.redstonecompiler.harness;

import java.util.Objects;

/** Tracks a consecutive, fully observed quiet window for one validation vector. */
final class TraceQuiescenceTracker {
    private final int RequiredUnchangedTicks;
    private final int TimeoutTicks;
    private final long StartGameTime;
    private long PreviousGameTime;
    private Object PreviousTraceState;
    private int UnchangedTicks;
    private int LastObservedChangeTick;
    private int UnobservedTickGapCount;
    private boolean Settled;

    TraceQuiescenceTracker(
            int RequiredUnchangedTicks,
            int TimeoutTicks,
            long StartGameTime,
            Object InitialTraceState) {
        if (RequiredUnchangedTicks <= 0) {
            throw new IllegalArgumentException("required-unchanged-ticks-must-be-positive");
        }
        if (TimeoutTicks < RequiredUnchangedTicks) {
            throw new IllegalArgumentException("timeout-must-cover-unchanged-window");
        }
        this.RequiredUnchangedTicks = RequiredUnchangedTicks;
        this.TimeoutTicks = TimeoutTicks;
        this.StartGameTime = StartGameTime;
        PreviousGameTime = StartGameTime;
        PreviousTraceState = InitialTraceState;
    }

    TraceQuiescenceStatus Observe(long GameTime, Object TraceState) {
        if (Settled) {
            throw new IllegalStateException("quiescence-already-settled");
        }
        if (GameTime < PreviousGameTime) {
            throw new IllegalArgumentException("game-time-moved-backward");
        }
        if (GameTime == PreviousGameTime) {
            return Status();
        }
        int ElapsedTicks = Math.toIntExact(GameTime - StartGameTime);
        if (GameTime != PreviousGameTime + 1) {
            UnchangedTicks = 0;
            UnobservedTickGapCount++;
        } else if (Objects.equals(TraceState, PreviousTraceState)) {
            UnchangedTicks++;
        } else {
            UnchangedTicks = 0;
            LastObservedChangeTick = ElapsedTicks;
        }
        PreviousGameTime = GameTime;
        PreviousTraceState = TraceState;
        Settled = UnchangedTicks >= RequiredUnchangedTicks;
        return Status();
    }

    TraceQuiescenceStatus Status() {
        int ElapsedTicks = Math.toIntExact(PreviousGameTime - StartGameTime);
        return new TraceQuiescenceStatus(
                ElapsedTicks,
                LastObservedChangeTick,
                UnchangedTicks,
                UnobservedTickGapCount,
                Settled,
                !Settled && ElapsedTicks >= TimeoutTicks);
    }

    record TraceQuiescenceStatus(
            int ElapsedTicks,
            int LastObservedChangeTick,
            int ObservedUnchangedTicks,
            int UnobservedTickGapCount,
            boolean Settled,
            boolean TimedOut) { }
}
