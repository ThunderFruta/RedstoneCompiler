//! Layered access and guide candidate construction.

use crate::Assignment::ParseContractRequirements;
use crate::Core::Deadline::{RuntimeDeadline, DEADLINE_CHECK_INTERVAL};
use crate::Core::Models::{
    AssignmentCandidate, AssignmentPoweredAccessConstraint, ClaimMask, ClaimMaskBuildError,
    Position,
};
use crate::Core::Runtime::RoutingThreadPool;
use pyo3::PyResult;
use rayon::prelude::*;
use std::cmp::Reverse;
use std::collections::{BTreeMap, BTreeSet, BinaryHeap, HashMap, HashSet, VecDeque};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Instant;

use super::State::*;
use super::Traversal::*;

mod Access;
pub(super) use Access::*;
mod GuideGeometry;
pub(super) use GuideGeometry::*;
mod PoweredWitness;
pub(super) use PoweredWitness::*;
mod AccessRamps;
pub(super) use AccessRamps::*;
#[macro_use]
mod PhysicalGuideEnumeration;
#[macro_use]
mod GuideEnumeration;
mod GuideDomain;
pub(super) use GuideDomain::*;
