//! Shared native worker-pool ownership and runtime telemetry.

use pyo3::prelude::*;
use rayon::{ThreadPool, ThreadPoolBuilder};
use std::sync::OnceLock;

pub(crate) fn RoutingThreadPool() -> &'static ThreadPool {
    static POOL: OnceLock<ThreadPool> = OnceLock::new();
    POOL.get_or_init(|| {
        let Available = std::thread::available_parallelism()
            .map(|Value| Value.get())
            .unwrap_or(1);
        let Requested = std::env::var("RC_ROUTING_THREADS")
            .ok()
            .and_then(|Value| Value.parse::<usize>().ok())
            .filter(|Value| *Value > 0)
            // Detailed negotiated routing shares this pool with portal and
            // legacy batch work. A moderate default leaves CPU headroom for
            // the Python coordinator; callers can override it explicitly.
            .unwrap_or(Available.min(8));
        ThreadPoolBuilder::new()
            .num_threads(Requested.clamp(1, Available))
            .thread_name(|Index| format!("redstone-router-{Index}"))
            .build()
            .expect("could not create native routing thread pool")
    })
}

#[pyfunction]
pub(crate) fn GetRoutingThreadCount() -> usize {
    RoutingThreadPool().current_num_threads()
}

// Instrument only test builds, at the per-item computation rather than the
// pool/batch telemetry boundary. Exact subprocess tests are its sole callers.
#[cfg(test)]
pub(crate) mod BatchExecutionWitness {
    use std::collections::HashSet;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Mutex;
    use std::thread::ThreadId;

    static ENABLED: AtomicBool = AtomicBool::new(false);
    static COMPUTATIONS: Mutex<Vec<ThreadId>> = Mutex::new(Vec::new());

    struct RecordingGuard;

    impl Drop for RecordingGuard {
        fn drop(&mut self) {
            ENABLED.store(false, Ordering::Release);
        }
    }

    pub(crate) fn RecordItem() {
        if ENABLED.load(Ordering::Acquire) {
            COMPUTATIONS
                .lock()
                .expect("batch computation witness")
                .push(std::thread::current().id());
        }
    }

    pub(crate) fn BatchSizes(Capacity: usize) -> Vec<usize> {
        let mut Sizes = vec![0, 1, Capacity - 1, Capacity, Capacity + 1, 2 * Capacity + 1];
        Sizes.sort_unstable();
        Sizes.dedup();
        Sizes
    }

    pub(crate) fn AssertBatch(
        Name: &str,
        Capacity: usize,
        ItemCount: usize,
        ComputeAndCheckResults: impl FnOnce(),
    ) {
        COMPUTATIONS.lock().expect("clear batch witness").clear();
        assert!(!ENABLED.swap(true, Ordering::AcqRel));
        let Recording = RecordingGuard;
        ComputeAndCheckResults();
        drop(Recording);
        let Calls = COMPUTATIONS.lock().expect("read batch witness");
        let Threads = Calls.iter().copied().collect::<HashSet<_>>();
        assert_eq!(Calls.len(), ItemCount, "{Name}: one computation per input");
        assert_eq!(
            Threads.len(),
            Capacity.min(ItemCount),
            "{Name}: actual computation threads for {ItemCount} inputs"
        );
    }
}

#[cfg(test)]
mod Tests {
    use super::{GetRoutingThreadCount, RoutingThreadPool};
    use std::collections::HashSet;
    use std::process::{Command, Stdio};
    use std::time::{Duration, Instant};

    #[test]
    fn native_pool_capacity_matches_host_and_request() {
        let ExpectedVariable = "RC_TEST_EXPECTED_POOL_THREADS";
        if let Ok(Expected) = std::env::var(ExpectedVariable) {
            let Expected = Expected.parse::<usize>().expect("exact test capacity");
            let Witnesses = RoutingThreadPool()
                .broadcast(|Context| (Context.index(), std::thread::current().id()));
            let Indices = Witnesses
                .iter()
                .map(|Value| Value.0)
                .collect::<HashSet<_>>();
            let Threads = Witnesses
                .iter()
                .map(|Value| Value.1)
                .collect::<HashSet<_>>();
            assert_eq!(GetRoutingThreadCount(), Expected);
            assert_eq!(Indices, (0..Expected).collect());
            assert_eq!(
                Threads.len(),
                Expected,
                "one real thread per admitted worker"
            );
            crate::Geometry::RouteClaims::AssertNativeBatchExecution(Expected);
            crate::Geometry::ExteriorConnectors::AssertNativeBatchExecution(Expected);
            return;
        }

        // This OS observation is independent of all production pool telemetry.
        // Every case starts a new process before OnceLock can observe its policy.
        let Available = std::thread::available_parallelism()
            .expect("OS capacity observation is required for this contract")
            .get();
        let Default = Available.min(8);
        let Cases = [
            (None, Default),
            (Some("1".to_owned()), 1),
            (Some("2".to_owned()), Available.min(2)),
            (Some("8".to_owned()), Available.min(8)),
            (Some("0".to_owned()), Default),
            (Some("not-a-count".to_owned()), Default),
            (Some(Available.saturating_add(1).to_string()), Available),
        ];
        for (Requested, Expected) in Cases {
            let mut Process = Command::new(std::env::current_exe().expect("test executable"));
            Process
                .args([
                    "--exact",
                    "Core::Runtime::Tests::native_pool_capacity_matches_host_and_request",
                    "--nocapture",
                ])
                .env_remove("RC_ROUTING_THREADS")
                .env(ExpectedVariable, Expected.to_string())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());
            if let Some(Value) = &Requested {
                Process.env("RC_ROUTING_THREADS", Value);
            }
            let mut Child = Process.spawn().expect("capacity test process");
            let Cutoff = Instant::now() + Duration::from_secs(15);
            while Child.try_wait().expect("capacity test status").is_none() {
                if Instant::now() >= Cutoff {
                    Child.kill().expect("stop stalled capacity test");
                    let Output = Child
                        .wait_with_output()
                        .expect("reap stalled capacity test");
                    panic!("capacity test exceeded watchdog: {Requested:?}: {Output:?}");
                }
                std::thread::sleep(Duration::from_millis(5));
            }
            let Output = Child.wait_with_output().expect("capacity test output");
            assert!(
                Output.status.success(),
                "requested={Requested:?}, OS allowance={Available}, expected={Expected}: {Output:?}"
            );
        }
    }
}
