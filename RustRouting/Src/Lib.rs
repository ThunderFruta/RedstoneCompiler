#![allow(non_snake_case)]

mod Assignment;
mod Core;
mod Escape;
mod Generation;
mod Geometry;
mod Path;
mod Planning;
mod Python;
mod Simulation;

use pyo3::types::PyModule;
use pyo3::{pymodule, Bound, PyResult};

#[pymodule]
fn RustRouting(Module: &Bound<'_, PyModule>) -> PyResult<()> {
    Python::Bindings::Register(Module)
}
