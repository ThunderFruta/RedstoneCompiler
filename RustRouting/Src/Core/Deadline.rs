//! Shared absolute-deadline contract for every native routing domain.

use std::sync::atomic::{AtomicBool, Ordering as AtomicOrdering};
use std::sync::Arc;
use std::time::{Duration, Instant};

pub(crate) const DEADLINE_CHECK_INTERVAL: usize = 32;

#[derive(Clone)]
pub(crate) struct RuntimeDeadline {
    EndsAt: Option<Instant>,
    Exceeded: Arc<AtomicBool>,
}

impl RuntimeDeadline {
    pub(crate) fn Unlimited() -> Self {
        Self {
            EndsAt: None,
            Exceeded: Arc::new(AtomicBool::new(false)),
        }
    }

    pub(crate) fn FromSeconds(MaximumRuntimeSeconds: Option<f64>) -> Result<Self, &'static str> {
        let Some(Seconds) = MaximumRuntimeSeconds else {
            return Ok(Self::Unlimited());
        };
        if !Seconds.is_finite() || Seconds < 0.0 {
            return Err("maximum runtime seconds must be finite and non-negative");
        }
        let DurationValue = Duration::try_from_secs_f64(Seconds)
            .map_err(|_Error| "maximum runtime seconds are out of range")?;
        Self::FromDuration(Some(DurationValue))
    }

    pub(crate) fn FromMilliseconds(
        MaximumRuntimeMilliseconds: Option<u64>,
    ) -> Result<Self, &'static str> {
        Self::FromDuration(MaximumRuntimeMilliseconds.map(Duration::from_millis))
    }

    fn FromDuration(MaximumRuntime: Option<Duration>) -> Result<Self, &'static str> {
        let Some(DurationValue) = MaximumRuntime else {
            return Ok(Self::Unlimited());
        };
        let EndsAt = Instant::now()
            .checked_add(DurationValue)
            .ok_or("maximum runtime is out of range")?;
        Ok(Self {
            EndsAt: Some(EndsAt),
            Exceeded: Arc::new(AtomicBool::new(false)),
        })
    }

    pub(crate) fn Check(&self) -> bool {
        if self.Exceeded.load(AtomicOrdering::Relaxed) {
            return true;
        }
        let IsExceeded = self
            .EndsAt
            .is_some_and(|DeadlineValue| Instant::now() >= DeadlineValue);
        if IsExceeded {
            self.Exceeded.store(true, AtomicOrdering::Relaxed);
        }
        IsExceeded
    }

    pub(crate) fn WasExceeded(&self) -> bool {
        self.Exceeded.load(AtomicOrdering::Relaxed)
    }

    pub(crate) fn RemainingMilliseconds(&self) -> Option<u64> {
        self.EndsAt.map(|DeadlineValue| {
            DeadlineValue
                .saturating_duration_since(Instant::now())
                .as_millis()
                .min(u128::from(u64::MAX)) as u64
        })
    }
}

#[cfg(test)]
mod Tests {
    use super::*;

    #[test]
    fn ZeroMillisecondDeadlineExpiresImmediately() {
        let Deadline = RuntimeDeadline::FromMilliseconds(Some(0)).unwrap();
        assert!(Deadline.Check());
        assert!(Deadline.WasExceeded());
    }

    #[test]
    fn LegacySecondDeadlineRejectsInvalidValues() {
        assert!(RuntimeDeadline::FromSeconds(Some(f64::NAN)).is_err());
        assert!(RuntimeDeadline::FromSeconds(Some(-1.0)).is_err());
    }
}
