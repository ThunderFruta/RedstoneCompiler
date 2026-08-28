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
