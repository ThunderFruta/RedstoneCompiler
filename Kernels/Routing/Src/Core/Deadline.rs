//! Shared absolute-deadline contract for every native routing domain.

#[cfg(test)]
use std::sync::atomic::AtomicUsize;
use std::sync::atomic::{AtomicBool, Ordering as AtomicOrdering};
use std::sync::Arc;
use std::time::{Duration, Instant};

pub(crate) const DEADLINE_CHECK_INTERVAL: usize = 32;

#[derive(Clone)]
pub(crate) struct RuntimeDeadline {
    EndsAt: Option<Instant>,
    Exceeded: Arc<AtomicBool>,
    #[cfg(test)]
    ChecksBeforeExpiry: Option<Arc<AtomicUsize>>,
}

impl RuntimeDeadline {
    pub(crate) fn Unlimited() -> Self {
        Self {
            EndsAt: None,
            Exceeded: Arc::new(AtomicBool::new(false)),
            #[cfg(test)]
            ChecksBeforeExpiry: None,
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
            #[cfg(test)]
            ChecksBeforeExpiry: None,
        })
    }

    /// Maps a caller-clock remaining duration onto an earlier native sample.
    /// The caller supplies a duration rounded down in its own clock domain, so
    /// time spent sampling and converting the cutoff is conservatively charged.
    pub(crate) fn FromEarlierSample(
        SampledAt: Instant,
        Remaining: Duration,
    ) -> Result<Self, &'static str> {
        let EndsAt = SampledAt
            .checked_add(Remaining)
            .ok_or("maximum runtime is out of range")?;
        Ok(Self {
            EndsAt: Some(EndsAt),
            Exceeded: Arc::new(AtomicBool::new(false)),
            #[cfg(test)]
            ChecksBeforeExpiry: None,
        })
    }

    #[cfg(test)]
    pub(crate) fn FromCheckBudget(SuccessfulChecks: usize) -> Self {
        Self {
            EndsAt: None,
            Exceeded: Arc::new(AtomicBool::new(false)),
            ChecksBeforeExpiry: Some(Arc::new(AtomicUsize::new(SuccessfulChecks))),
        }
    }

    pub(crate) fn Check(&self) -> bool {
        if self.Exceeded.load(AtomicOrdering::Relaxed) {
            return true;
        }
        #[cfg(test)]
        if let Some(ChecksBeforeExpiry) = &self.ChecksBeforeExpiry {
            let Previous = ChecksBeforeExpiry
                .fetch_update(AtomicOrdering::Relaxed, AtomicOrdering::Relaxed, |Value| {
                    Value.checked_sub(1)
                })
                .unwrap_or(0);
            if Previous == 0 {
                self.Exceeded.store(true, AtomicOrdering::Relaxed);
                return true;
            }
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
