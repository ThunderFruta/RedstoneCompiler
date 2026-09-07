//! Exact per-request admission for authoritative route and proof expansion work.

use std::sync::atomic::{AtomicUsize, Ordering};

#[derive(Clone, Copy)]
pub(crate) enum ExpansionWorkPhase {
    Route,
    Proof,
}

pub(crate) struct RequestExpansionAdmissionV1 {
    Maximum: usize,
    Total: AtomicUsize,
    Route: AtomicUsize,
    Proof: AtomicUsize,
}

impl RequestExpansionAdmissionV1 {
    pub(crate) fn New(Maximum: usize) -> Self {
        Self {
            Maximum,
            Total: AtomicUsize::new(0),
            Route: AtomicUsize::new(0),
            Proof: AtomicUsize::new(0),
        }
    }

    pub(crate) fn TryAdmitOne(&self, Phase: ExpansionWorkPhase) -> bool {
        loop {
            let Current = self.Total.load(Ordering::Relaxed);
            if Current >= self.Maximum {
                return false;
            }
            let Some(Next) = Current.checked_add(1) else {
                return false;
            };
            if self
                .Total
                .compare_exchange_weak(Current, Next, Ordering::Relaxed, Ordering::Relaxed)
                .is_ok()
            {
                match Phase {
                    ExpansionWorkPhase::Route => {
                        let Previous = self.Route.fetch_add(1, Ordering::Relaxed);
                        assert!(Previous < self.Maximum, "route work counter overflow");
                    }
                    ExpansionWorkPhase::Proof => {
                        let Previous = self.Proof.fetch_add(1, Ordering::Relaxed);
                        assert!(Previous < self.Maximum, "proof work counter overflow");
                    }
                }
                return true;
            }
        }
    }

    pub(crate) fn Maximum(&self) -> usize {
        self.Maximum
    }

    pub(crate) fn RouteCount(&self) -> usize {
        self.Route.load(Ordering::Relaxed)
    }

    pub(crate) fn ProofCount(&self) -> usize {
        self.Proof.load(Ordering::Relaxed)
    }

    pub(crate) fn TotalCount(&self) -> usize {
        self.Total.load(Ordering::Relaxed)
    }
}

#[cfg(test)]
mod Tests {
    use super::*;

    #[test]
    fn SharedAdmissionNeverExceedsExactMaximum() {
        let Admission = RequestExpansionAdmissionV1::New(2);
        assert!(Admission.TryAdmitOne(ExpansionWorkPhase::Route));
        assert!(Admission.TryAdmitOne(ExpansionWorkPhase::Proof));
        assert!(!Admission.TryAdmitOne(ExpansionWorkPhase::Route));
        assert_eq!(Admission.RouteCount(), 1);
        assert_eq!(Admission.ProofCount(), 1);
        assert_eq!(Admission.TotalCount(), 2);
    }

    #[test]
    fn ZeroMaximumAdmitsNoWork() {
        let Admission = RequestExpansionAdmissionV1::New(0);
        assert!(!Admission.TryAdmitOne(ExpansionWorkPhase::Route));
        assert!(!Admission.TryAdmitOne(ExpansionWorkPhase::Proof));
        assert_eq!(Admission.TotalCount(), 0);
    }
}
