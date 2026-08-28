//! Exact layered access/guide catalog selection.

use crate::Assignment::{SelectionHasPoweredAccessWitnessExact, SortCandidatesWithDeadline};
use crate::Core::Deadline::RuntimeDeadline;
use crate::Core::Models::{AssignmentCandidate, RoutingAssignmentResult};
use crate::Core::Runtime::RoutingThreadPool;
use crate::Planning::AssignmentPlanning::PlanAuthoritativeCandidateGroupsWithInitialExpansionDeadlineAndCrossAir;
use pyo3::PyResult;
use rayon::prelude::*;
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::sync::Arc;

mod Search;
pub(super) use Search::*;
mod BundleDomain;
pub(super) use BundleDomain::*;
#[macro_use]
mod SolverPreparation;
mod Solver;
pub(super) use Solver::*;
